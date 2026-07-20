"""Task queue + kanban board for voice-dispatched Grok agent runs.

Runs on the Pi, next to Jarvis. Jarvis posts tasks here by voice; a worker on
the workstation (where grok and the repos live) polls, claims, and reports back.

The pull direction is deliberate: the workstation is WSL behind NAT, so the Pi
cannot reach it. The worker calling out avoids any port forwarding, and keeps
working when the WSL IP changes on restart — which it does.

    POST /api/tasks              queue a task            (Jarvis)
    GET  /api/tasks/claim        claim the next one      (worker)
    POST /api/tasks/<id>/status  report progress/result  (worker)
    GET  /api/tasks              board state             (UI)
    POST /api/tasks/<id>/cancel  cancel a queued task
    GET  /                       the board
"""
from __future__ import annotations
import json
import os
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

STATE_FILE = Path(os.environ.get("TASKBOARD_STATE", "/data/tasks.json"))
# A task the worker claimed but never reported on — the worker died, the
# workstation slept, WSL restarted. Without this they'd sit in "running"
# forever and the board would lie.
STALE_AFTER_S = float(os.environ.get("TASKBOARD_STALE_AFTER_S", "1800"))
MAX_TASKS = int(os.environ.get("TASKBOARD_MAX_TASKS", "200"))

STATES = ("queued", "running", "done", "failed", "cancelled")

app = Flask(__name__)
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return []


def _save(tasks: list[dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tasks, indent=2))
    tmp.replace(STATE_FILE)  # atomic; a torn write would lose the whole board


def _expire(tasks: list[dict]) -> None:
    now = time.time()
    for t in tasks:
        if t["state"] == "running" and now - t.get("claimed_at", now) > STALE_AFTER_S:
            t["state"] = "failed"
            t["error"] = f"worker went silent for over {STALE_AFTER_S / 60:.0f} minutes"
            t["finished_at"] = now


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/tasks")
def list_tasks():
    with _lock:
        tasks = _load()
        _expire(tasks)
        _save(tasks)
    counts = {s: sum(1 for t in tasks if t["state"] == s) for s in STATES}
    return jsonify({"tasks": tasks, "counts": counts,
                    "cost_usd": round(sum(t.get("cost_usd") or 0 for t in tasks), 4)})


@app.post("/api/tasks")
def create_task():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    task = {
        "id": uuid.uuid4().hex[:8],
        "prompt": prompt[:2000],
        "repo": (data.get("repo") or "").strip(),
        "model": (data.get("model") or "").strip(),
        "worktree": bool(data.get("worktree", True)),
        "source": (data.get("source") or "voice").strip(),
        "state": "queued",
        "created_at": time.time(),
    }
    with _lock:
        tasks = _load()
        tasks.insert(0, task)
        del tasks[MAX_TASKS:]
        _save(tasks)
    return jsonify(task), 201


@app.get("/api/tasks/claim")
def claim_task():
    """Hand the oldest queued task to a worker. Claiming is a state change, so
    two workers can't take the same task."""
    worker = request.args.get("worker", "unknown")
    with _lock:
        tasks = _load()
        _expire(tasks)
        for task in sorted((t for t in tasks if t["state"] == "queued"),
                           key=lambda t: t["created_at"]):
            task["state"] = "running"
            task["worker"] = worker
            task["claimed_at"] = time.time()
            _save(tasks)
            return jsonify(task)
        _save(tasks)
    return jsonify({}), 204


@app.post("/api/tasks/<task_id>/status")
def update_task(task_id: str):
    data = request.get_json(silent=True) or {}
    state = data.get("state")
    if state not in STATES:
        return jsonify({"error": f"state must be one of {STATES}"}), 400
    with _lock:
        tasks = _load()
        for task in tasks:
            if task["id"] != task_id:
                continue
            task["state"] = state
            for field in ("output", "error", "session_id", "branch", "model"):
                if data.get(field) is not None:
                    task[field] = str(data[field])[:8000]
            if data.get("cost_usd") is not None:
                task["cost_usd"] = float(data["cost_usd"])
            if data.get("turns") is not None:
                task["turns"] = int(data["turns"])
            if state in ("done", "failed", "cancelled"):
                task["finished_at"] = time.time()
            _save(tasks)
            return jsonify(task)
    return jsonify({"error": "no such task"}), 404


@app.post("/api/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    with _lock:
        tasks = _load()
        for task in tasks:
            if task["id"] == task_id and task["state"] == "queued":
                task["state"] = "cancelled"
                task["finished_at"] = time.time()
                _save(tasks)
                return jsonify(task)
        # a running task is the worker's to stop; the board can't reach into it
        return jsonify({"error": "only queued tasks can be cancelled"}), 409


@app.post("/api/tasks/clear")
def clear_finished():
    with _lock:
        tasks = [t for t in _load() if t["state"] in ("queued", "running")]
        _save(tasks)
    return jsonify({"remaining": len(tasks)})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
            threaded=True)
