# grok worker

Runs on the workstation (where `grok` and the repos are). Polls the task board
on the Pi, claims work, runs the agent, reports back.

```bash
# one task then exit — good for testing
GROK_BOARD_URL=http://172.16.10.117:8090 python3 grok_worker.py --once

# what voice names resolve to
python3 grok_worker.py --list-repos

# poll forever
python3 grok_worker.py
```

The worker **calls out** rather than listening. WSL sits behind NAT with an IP
that changes on restart, so nothing on the LAN can reach it — polling sidesteps
that entirely and needs no port forwarding.

| Env | Default | |
|---|---|---|
| `GROK_BOARD_URL` | `http://172.16.10.117:8090` | the Pi's board |
| `GROK_PROJECTS_DIR` | `~/Documents/projects` | where repos live |
| `GROK_DEFAULT_REPO` | *(none)* | used when the task names no repo |
| `GROK_TASK_TIMEOUT_S` | `1800` | kill a run after this long |
| `GROK_POLL_S` | `5` | claim interval |

## Notes

Tasks run in a **git worktree** on a `grok/<id>` branch, so a dispatched agent
never touches your working checkout — you usually have uncommitted changes, and
an agent landing in them is a bad day.

Repos are an allowlist built from `GROK_PROJECTS_DIR`. A voice transcript is
untrusted input; `--cwd` pointed anywhere would let a mis-heard word send an
agent somewhere it has no business being. Both the folder name and the git
remote name are aliases, since they differ (this repo is `spiderman` on disk
and `jarvis` on GitHub).

Every run costs real money — about $0.02 for a trivial one. The board totals it.
