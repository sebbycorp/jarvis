"""Grok worker — runs on the workstation, takes work from the Pi.

The board and queue live on the Pi (with Jarvis); grok and the repos live here.
This polls the Pi, claims a task, runs `grok` headless in the named repo, and
reports the result back. Nothing listens on this machine: WSL is behind NAT and
its IP changes on restart, so the worker calls out rather than being called.

    python3 grok_worker.py                 # poll forever
    python3 grok_worker.py --once          # single task, for testing
    python3 grok_worker.py --list-repos    # show what voice names map to

Repos are an explicit allowlist. A voice transcript is untrusted input, and
`--cwd` pointed at an arbitrary path would let a mis-heard word send an agent
loose somewhere it has no business being.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BOARD = os.environ.get("GROK_BOARD_URL", "http://172.16.10.117:8090")
WORKER = os.environ.get("GROK_WORKER_NAME", os.uname().nodename)
POLL_S = float(os.environ.get("GROK_POLL_S", "5"))
TASK_TIMEOUT_S = float(os.environ.get("GROK_TASK_TIMEOUT_S", "1800"))
PROJECTS = Path(os.environ.get("GROK_PROJECTS_DIR",
                               str(Path.home() / "Documents/projects")))
DEFAULT_REPO = os.environ.get("GROK_DEFAULT_REPO", "")

try:
    import requests
except ImportError:
    sys.exit("worker needs requests: pip install requests")


def _remote_name(repo: Path) -> str:
    """The repo's name on its remote, which often differs from the folder.
    (This project's folder is `spiderman` while GitHub calls it `jarvis`.)"""
    try:
        out = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return ""
    if out.returncode != 0:
        return ""
    url = out.stdout.strip().removesuffix(".git")
    return url.rsplit("/", 1)[-1].lower() if url else ""


def repos() -> dict[str, Path]:
    """Voice-friendly name -> path, for every git repo under PROJECTS.

    Both the folder name and the remote's name are registered as aliases, so
    asking for either resolves.
    """
    found: dict[str, Path] = {}
    if not PROJECTS.is_dir():
        return found
    for entry in sorted(PROJECTS.iterdir()):
        if not (entry / ".git").exists():
            continue
        found[entry.name.lower()] = entry
        remote = _remote_name(entry)
        if remote:
            found.setdefault(remote, entry)
    return found


def match_repo(spoken: str) -> Path | None:
    """Resolve a repo from a transcript. Speech mangles names — "k8s goose"
    for k8s-goose, "the jarvis repo" — so match on normalised words rather
    than requiring an exact string."""
    available = repos()
    if not spoken:
        spoken = DEFAULT_REPO
    if not spoken:
        return None
    want = re.sub(r"[^a-z0-9]+", " ", spoken.lower()).strip()
    if not want:
        return None
    if want.replace(" ", "-") in available:
        return available[want.replace(" ", "-")]
    for name, path in available.items():
        flat = re.sub(r"[^a-z0-9]+", " ", name).strip()
        if flat == want or want in flat or flat in want:
            return path
    # last resort: every word of the query appears in the repo name
    words = want.split()
    for name, path in available.items():
        flat = re.sub(r"[^a-z0-9]+", " ", name)
        if all(w in flat for w in words):
            return path
    return None


def report(task_id: str, **fields) -> None:
    try:
        requests.post(f"{BOARD}/api/tasks/{task_id}/status", json=fields, timeout=20)
    except requests.RequestException as e:
        print(f"  ! could not report {task_id}: {e}")


def run_task(task: dict) -> None:
    tid = task["id"]
    prompt = task["prompt"]
    repo = match_repo(task.get("repo", ""))
    print(f"\n▶ {tid}  {prompt[:70]}")

    if repo is None:
        known = ", ".join(sorted(repos())) or "none found"
        report(tid, state="failed",
               error=f"no repo matching {task.get('repo')!r}. Known: {known}")
        print(f"  ✗ unknown repo {task.get('repo')!r}")
        return

    branch = f"grok/{tid}"
    cmd = ["grok", "-p", prompt, "--output-format", "json",
           "--cwd", str(repo), "--permission-mode", "acceptEdits"]
    if task.get("worktree", True):
        # Keep dispatched work off the user's checkout — they often have
        # uncommitted changes, and an agent landing in them is a bad day.
        cmd += ["--worktree", branch]
    if task.get("model"):
        cmd += ["--model", task["model"]]

    report(tid, state="running", branch=branch if task.get("worktree", True) else "",
           model=task.get("model") or "")
    started = time.time()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=TASK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        report(tid, state="failed",
               error=f"timed out after {TASK_TIMEOUT_S / 60:.0f} minutes")
        print("  ✗ timeout")
        return
    except FileNotFoundError:
        report(tid, state="failed", error="grok CLI not found on the worker")
        print("  ✗ grok not found")
        return

    elapsed = time.time() - started
    payload = {}
    for line in reversed(out.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not payload and out.stdout.strip().startswith("{"):
        try:
            payload = json.loads(out.stdout)
        except json.JSONDecodeError:
            payload = {}

    if out.returncode != 0 and not payload:
        err = (out.stderr or out.stdout or "grok failed").strip()
        report(tid, state="failed", error=err[-2000:])
        print(f"  ✗ exit {out.returncode}")
        return

    text = payload.get("text") or out.stdout.strip()
    stop = payload.get("stopReason", "")
    failed = out.returncode != 0 or stop in ("Error", "MaxTurns")
    report(tid,
           state="failed" if failed else "done",
           output=text[-6000:],
           error=stop if failed else None,
           session_id=payload.get("sessionId", ""),
           cost_usd=payload.get("total_cost_usd"),
           turns=payload.get("num_turns"),
           model=", ".join(payload.get("modelUsage", {})) or task.get("model") or "")
    cost = payload.get("total_cost_usd") or 0
    print(f"  {'✗' if failed else '✓'} {elapsed:.0f}s  ${cost:.4f}  {text[:70]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one task and exit")
    ap.add_argument("--list-repos", action="store_true")
    args = ap.parse_args()

    if args.list_repos:
        found = repos()
        print(f"repos under {PROJECTS}:")
        for name, path in found.items():
            print(f"  {name:24} {path}")
        if not found:
            print("  (none — set GROK_PROJECTS_DIR)")
        return

    print(f"grok worker '{WORKER}' -> {BOARD}")
    print(f"  repos: {', '.join(repos()) or 'none'}")
    warned = False
    while True:
        try:
            r = requests.get(f"{BOARD}/api/tasks/claim",
                             params={"worker": WORKER}, timeout=20)
            warned = False
            if r.status_code == 200 and r.json().get("id"):
                run_task(r.json())
                if args.once:
                    return
                continue
        except requests.RequestException as e:
            if not warned:  # the Pi rebooting shouldn't spam the console
                print(f"  board unreachable ({e}); retrying quietly")
                warned = True
        except KeyboardInterrupt:
            print("\nbye")
            return
        if args.once:
            print("no queued tasks")
            return
        try:
            time.sleep(POLL_S)
        except KeyboardInterrupt:
            print("\nbye")
            return


if __name__ == "__main__":
    main()
