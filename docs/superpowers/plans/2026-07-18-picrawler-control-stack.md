# PiCrawler Control Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring a SunFounder PiCrawler (Raspberry Pi 4) fully operational and expose control through a shared Python core, an MCP server (primary remote interface), a Claude Code skill, a web panel, and an OpenAI voice+video assistant.

**Architecture:** All interfaces are thin front-ends over one module, `picrawler_ctl.py`, which wraps the SunFounder `picrawler`/`robot_hat`/`vilib` libraries. Code is developed in this git repo and deployed to the Pi at `~/picrawler-app/` over SSH. The MCP server runs as a systemd service on `0.0.0.0:8000`.

**Tech Stack:** Raspberry Pi OS (Debian 13 Trixie, Python 3.13), SunFounder `robot-hat`/`vilib`/`picrawler`, FastMCP (streamable-HTTP), Flask, OpenAI Python SDK, systemd.

**Target:** Pi 4 at `172.16.10.117`, user `smaniak`, sudo password `W3lcome098!`.

---

## Conventions used throughout this plan

**SSH/sudo helper.** All remote commands run from the laptop. Export the password once per shell session:

```bash
export SSHPASS='W3lcome098!'
PI="smaniak@172.16.10.117"
ssh_pi()  { sshpass -e ssh -o StrictHostKeyChecking=no "$PI" "$@"; }
# sudo over SSH (non-interactive): pipe the password to sudo -S
ssh_sudo(){ sshpass -e ssh -o StrictHostKeyChecking=no "$PI" "echo '$SSHPASS' | sudo -S $*"; }
```

Paste those four lines into any shell before running remote steps in this plan.

**App location on Pi:** `~/picrawler-app/` (i.e. `/home/smaniak/picrawler-app/`).
**Virtualenv on Pi:** `~/picrawler-app/.venv` (created `--system-site-packages`).
**Commit after each task.** Secrets (`.env`) are never committed and never synced.

---

## File Structure

```
spiderman/
├── robot/                       # synced to Pi:~/picrawler-app/
│   ├── picrawler_ctl.py         # Task 2.x — shared core API
│   ├── mcp_server.py            # Task 3.x — FastMCP server
│   ├── teleop.py                # Task 2.x — keyboard drive
│   ├── requirements.txt         # Task 1.x — pip deps for the venv
│   ├── config.example.env       # Task 3.x — env template
│   ├── web/
│   │   ├── app.py               # Task 5.x — Flask panel + MJPEG
│   │   └── templates/index.html # Task 5.x — control UI
│   └── ai_assistant.py          # Task 6.x — OpenAI voice+video loop
├── scripts/
│   ├── setup_pi.sh              # Task 1.x — I2C + lib install driver
│   ├── deploy.sh                # Task 2.x — rsync repo → Pi
│   └── picrawler-mcp.service    # Task 3.x — systemd unit
└── docs/superpowers/
    ├── specs/2026-07-18-picrawler-control-stack-design.md
    └── plans/2026-07-18-picrawler-control-stack.md   (this file)
```

The Claude Code skill is created on the laptop at `~/.claude/skills/picrawler-control/` (Task 4.x) — outside the repo.

---

# Phase 0 — Hardware Enablement (I2C)

**Physical prerequisite:** none yet (no movement). Requires a reboot of the Pi.

### Task 0.1: Back up and edit boot config to enable I2C

**Files:** none in repo (remote edit of `/boot/firmware/config.txt`).

- [ ] **Step 1: Confirm current state (I2C not enabled)**

Run:
```bash
ssh_pi "ls /dev/i2c-1 2>&1; grep -n 'dtparam=i2c_arm' /boot/firmware/config.txt"
```
Expected: `ls: cannot access '/dev/i2c-1': No such file or directory` and a line `#dtparam=i2c_arm=on` (commented).

- [ ] **Step 2: Back up config.txt**

Run:
```bash
ssh_sudo "cp /boot/firmware/config.txt /boot/firmware/config.txt.bak.$(date +%s)"
ssh_pi  "ls -la /boot/firmware/config.txt.bak.*"
```
Expected: a backup file is listed.

- [ ] **Step 3: Enable i2c_arm (uncomment or append)**

Run:
```bash
ssh_sudo "sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' /boot/firmware/config.txt"
ssh_sudo "grep -q '^dtparam=i2c_arm=on' /boot/firmware/config.txt || echo 'dtparam=i2c_arm=on' | tee -a /boot/firmware/config.txt"
ssh_pi  "grep -n '^dtparam=i2c_arm=on' /boot/firmware/config.txt"
```
Expected: prints an uncommented `dtparam=i2c_arm=on` line.

- [ ] **Step 4: Ensure i2c-dev module loads at boot and install i2c-tools**

Run:
```bash
ssh_sudo "grep -qx 'i2c-dev' /etc/modules || echo 'i2c-dev' | tee -a /etc/modules"
ssh_sudo "DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y i2c-tools"
```
Expected: apt finishes, `i2c-tools` installed.

### Task 0.2: Reboot and verify the Robot HAT is on the bus

- [ ] **Step 1: Warn the user, then reboot**

> ⚠️ Tell the user: "Rebooting the Pi now to enable I2C. It'll be back in ~30–60s."

Run:
```bash
ssh_sudo "reboot" || true
```
Expected: connection drops (normal).

- [ ] **Step 2: Wait for the Pi to come back**

Run:
```bash
until ssh_pi "echo up" 2>/dev/null; do sleep 5; done; echo "Pi is back"
```
Expected: eventually prints `up` then `Pi is back`.

- [ ] **Step 3: Verify `/dev/i2c-1` exists and scan the bus**

Run:
```bash
ssh_pi "ls /dev/i2c-1"
ssh_sudo "i2cdetect -y 1"
```
Expected: `/dev/i2c-1` exists; the grid shows a device (SunFounder Robot HAT typically at `14`, and/or the PWM/servo controller at `40`). **Record the addresses you see** — non-empty grid means the HAT is talking. If the grid is entirely empty, STOP: check the HAT is seated and powered (battery on) before continuing.

- [ ] **Step 4: Commit a note of the observed addresses (repo doc)**

Create `docs/hardware-notes.md` with the `i2cdetect` output you observed, then:
```bash
git add docs/hardware-notes.md && git commit -m "docs: record I2C bus scan after enabling i2c_arm"
```

---

# Phase 1 — Install the SunFounder Stack

**Goal:** `python -c "import robot_hat, vilib, picrawler"` succeeds inside the venv.

### Task 1.1: Confirm the official install procedure (guard against Trixie drift)

- [ ] **Step 1: Fetch the current SunFounder install docs**

Use WebFetch on `https://docs.sunfounder.com/projects/pi-crawler/en/latest/python/python_start/install_all_modules.html` and on the three repos:
`https://github.com/sunfounder/robot-hat`, `https://github.com/sunfounder/vilib`, `https://github.com/sunfounder/picrawler`.
Confirm the install commands below still match (repo URLs, `setup.py` vs `install.py`, any new `apt` deps). If they diverge, update Task 1.3 accordingly before running it.

### Task 1.2: Create the app dir and a system-site venv on the Pi

- [ ] **Step 1: Install base apt dependencies**

Run:
```bash
ssh_sudo "DEBIAN_FRONTEND=noninteractive apt-get install -y git python3-venv python3-pip python3-picamera2 python3-libcamera python3-dev build-essential portaudio19-dev libsdl2-mixer-2.0-0 ffmpeg"
```
Expected: apt completes. (`python3-picamera2`/`python3-libcamera` are the system camera stack `vilib` needs; `portaudio19-dev` is for mic capture; `ffmpeg` for audio; SDL2 mixer for robot_hat sound.)

- [ ] **Step 2: Create app dir + venv with system site packages**

Run:
```bash
ssh_pi "mkdir -p ~/picrawler-app && python3 -m venv --system-site-packages ~/picrawler-app/.venv"
ssh_pi "~/picrawler-app/.venv/bin/python -c 'import picamera2; print(\"picamera2 visible:\", picamera2.__name__)'"
```
Expected: `picamera2 visible: picamera2` — confirms the venv sees the system camera lib.

### Task 1.3: Install robot-hat, vilib, picrawler from source

- [ ] **Step 1: Clone the three repos**

Run:
```bash
ssh_pi "mkdir -p ~/sf && cd ~/sf && \
  (test -d robot-hat || git clone https://github.com/sunfounder/robot-hat.git) && \
  (test -d vilib     || git clone https://github.com/sunfounder/vilib.git) && \
  (test -d picrawler || git clone https://github.com/sunfounder/picrawler.git) && \
  ls"
```
Expected: `picrawler robot-hat vilib` listed.

- [ ] **Step 2: Install robot-hat into the venv**

Run:
```bash
ssh_pi "cd ~/sf/robot-hat && ~/picrawler-app/.venv/bin/pip install ."
```
Expected: `Successfully installed robot-hat-...`. If it fails on a pinned dep, retry with `--no-build-isolation` or edit the offending pin (note it in `docs/hardware-notes.md`).

- [ ] **Step 3: Install vilib into the venv**

Run:
```bash
ssh_pi "cd ~/sf/vilib && ~/picrawler-app/.venv/bin/pip install ."
```
Expected: `Successfully installed vilib-...`. (vilib's `install.py` mainly does apt work we already did in Task 1.2; `pip install .` installs the Python package.)

- [ ] **Step 4: Install picrawler into the venv**

Run:
```bash
ssh_pi "cd ~/sf/picrawler && ~/picrawler-app/.venv/bin/pip install ."
```
Expected: `Successfully installed picrawler-...`.

- [ ] **Step 5: Import smoke test**

Run:
```bash
ssh_pi "~/picrawler-app/.venv/bin/python -c 'import robot_hat, vilib, picrawler; from picrawler import Picrawler; print(\"imports OK\")'"
```
Expected: `imports OK`. If any import fails, this is the **Trixie compatibility risk** materializing — capture the traceback, try the source fix, and if unresolvable escalate the Bookworm-reflash fallback to the user (do NOT reflash without approval).

### Task 1.4: Enable the onboard speaker (I2S amp)

- [ ] **Step 1: Run SunFounder's i2samp speaker setup**

Run:
```bash
ssh_pi "cd ~/sf/robot-hat && ls i2samp.sh 2>/dev/null && echo found || echo 'no i2samp — check docs'"
```
If present:
```bash
ssh_pi "cd ~/sf/robot-hat && echo -e 'y\ny\ny' | sudo ./i2samp.sh" || true
```
Expected: script configures the I2S amp. This may request another reboot — if so, reboot (Task 0.2 Steps 1–2 pattern) and re-verify imports. If `i2samp.sh` is absent, the USB sound device (card 3) remains available as an output fallback; note this and continue.

- [ ] **Step 2: Record requirements.txt in the repo**

Create `robot/requirements.txt`:
```
# Installed from source on the Pi (not via this file), pinned here for reference:
#   robot-hat, vilib, picrawler  -> from github.com/sunfounder
# Pure-pip deps for our own code:
fastmcp>=2.0
flask>=3.0
openai>=1.30
sounddevice>=0.4
numpy>=1.26
Pillow>=10.0
```
Commit:
```bash
git add robot/requirements.txt && git commit -m "chore: record Pi python requirements"
```

---

# Phase 2 — Core API + First Walk

**Physical prerequisite:** robot assembled, battery ON, clear floor space.

### Task 2.1: Discover the real PiCrawler action API on the device

- [ ] **Step 1: Introspect the installed picrawler package**

Run:
```bash
ssh_pi "~/picrawler-app/.venv/bin/python - <<'PY'
from picrawler import Picrawler
import inspect
print('methods:', [m for m in dir(Picrawler) if not m.startswith('_')])
try:
    print(inspect.getsource(Picrawler.do_action))
except Exception as e:
    print('no do_action source:', e)
PY"
```
Expected: a method list including `do_action` (and likely `do_step`, `move_list`/`servo` helpers). **Record the exact action strings** `do_action` accepts (commonly `'forward'`, `'backward'`, `'turn left'`, `'turn right'`, `'stand'`, `'sit'`). If names differ, adjust `ACTION_MAP` in Task 2.2 to match.

### Task 2.2: Write `picrawler_ctl.py` (the shared core)

**Files:**
- Create: `robot/picrawler_ctl.py`

- [ ] **Step 1: Write the module**

Create `robot/picrawler_ctl.py`:
```python
"""picrawler_ctl — one clean, safe API over the SunFounder PiCrawler stack.

Every front-end (MCP server, web panel, AI assistant, teleop) imports THIS.
It is the single point of hardware access so callers never fight over the HAT.
"""
from __future__ import annotations
import os
import time
import threading

# Action name mapping — verify against Task 2.1 output on the device.
ACTION_MAP = {
    "forward": "forward",
    "backward": "backward",
    "turn_left": "turn left",
    "turn_right": "turn right",
    "stand": "stand",
    "rest": "sit",
}

DEFAULT_SPEED = 80
PHOTO_DIR = os.path.expanduser("~/picrawler-app/photos")


class PiCrawlerController:
    """Thread-safe wrapper. Instantiate ONCE per process."""

    def __init__(self, speed: int = DEFAULT_SPEED):
        from picrawler import Picrawler  # imported lazily so tests can run w/o hw
        self._crawler = Picrawler()
        self._lock = threading.Lock()
        self._speed = self._clamp_speed(speed)
        self._camera_on = False
        os.makedirs(PHOTO_DIR, exist_ok=True)

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _clamp_speed(speed: int) -> int:
        return max(1, min(100, int(speed)))

    def set_speed(self, speed: int) -> int:
        with self._lock:
            self._speed = self._clamp_speed(speed)
            return self._speed

    def _act(self, action_key: str, steps: int, speed: int | None):
        if action_key not in ACTION_MAP:
            raise ValueError(f"unknown action {action_key!r}; "
                             f"valid: {sorted(ACTION_MAP)}")
        spd = self._clamp_speed(speed if speed is not None else self._speed)
        steps = max(1, min(20, int(steps)))
        with self._lock:
            for _ in range(steps):
                self._crawler.do_action(ACTION_MAP[action_key], 1, spd)
                time.sleep(0.02)
        return {"action": action_key, "steps": steps, "speed": spd}

    # ---- movement ---------------------------------------------------------
    def forward(self, steps: int = 1, speed: int | None = None):
        return self._act("forward", steps, speed)

    def backward(self, steps: int = 1, speed: int | None = None):
        return self._act("backward", steps, speed)

    def turn_left(self, steps: int = 1, speed: int | None = None):
        return self._act("turn_left", steps, speed)

    def turn_right(self, steps: int = 1, speed: int | None = None):
        return self._act("turn_right", steps, speed)

    def stand(self, speed: int | None = None):
        return self._act("stand", 1, speed)

    def rest(self, speed: int | None = None):
        return self._act("rest", 1, speed)

    def stop(self):
        """Leave the robot in a safe standing pose."""
        with self._lock:
            self._crawler.do_action(ACTION_MAP["stand"], 1, self._speed)
        return {"stopped": True}

    # ---- camera -----------------------------------------------------------
    def _ensure_camera(self):
        if not self._camera_on:
            from vilib import Vilib
            Vilib.camera_start(vflip=False, hflip=False)
            Vilib.display(local=False, web=False)
            time.sleep(1.0)
            self._camera_on = True

    def photo(self, name: str | None = None) -> str:
        from vilib import Vilib
        self._ensure_camera()
        name = name or f"photo_{int(time.time())}"
        Vilib.take_photo(name, PHOTO_DIR)
        time.sleep(0.3)
        return os.path.join(PHOTO_DIR, f"{name}.jpg")

    def capture_jpeg_bytes(self) -> bytes:
        """Return the latest frame as JPEG bytes (for MJPEG / MCP vision)."""
        import cv2
        from vilib import Vilib
        self._ensure_camera()
        frame = Vilib.img  # BGR numpy array maintained by vilib
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("failed to encode camera frame")
        return buf.tobytes()

    # ---- audio ------------------------------------------------------------
    def speak(self, text: str) -> dict:
        from robot_hat import TTS
        with self._lock:
            TTS().say(text)
        return {"spoke": text}

    # ---- status -----------------------------------------------------------
    def status(self) -> dict:
        return {"speed": self._speed, "camera_on": self._camera_on}


# Module-level singleton accessor
_controller: PiCrawlerController | None = None

def get_controller() -> PiCrawlerController:
    global _controller
    if _controller is None:
        _controller = PiCrawlerController()
    return _controller
```

- [ ] **Step 2: Write `robot/teleop.py` (manual keyboard drive)**

Create `robot/teleop.py`:
```python
"""Keyboard teleop for manual testing. Run on the Pi:
   ~/picrawler-app/.venv/bin/python ~/picrawler-app/teleop.py
Keys: w/s forward/back, a/d turn, space stand, r rest, q quit."""
import sys, tty, termios
from picrawler_ctl import get_controller

def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

def main():
    c = get_controller()
    print("teleop ready — w/s/a/d, space=stand, r=rest, q=quit")
    keymap = {"w": c.forward, "s": c.backward, "a": c.turn_left,
              "d": c.turn_right, " ": c.stand, "r": c.rest}
    while True:
        k = getch().lower()
        if k == "q":
            c.stop(); print("bye"); break
        fn = keymap.get(k)
        if fn:
            print(fn())

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write `scripts/deploy.sh`**

Create `scripts/deploy.sh`:
```bash
#!/usr/bin/env bash
# Deploy robot/ -> Pi:~/picrawler-app/ (excludes secrets & venv & photos)
set -euo pipefail
: "${SSHPASS:?export SSHPASS first}"
PI="${PI:-smaniak@172.16.10.117}"
SRC="$(cd "$(dirname "$0")/../robot" && pwd)/"
sshpass -e rsync -az --delete \
  --exclude '.venv' --exclude '.env' --exclude 'photos' --exclude '__pycache__' \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$SRC" "$PI:~/picrawler-app/"
echo "deployed $SRC -> $PI:~/picrawler-app/"
```
Make executable and commit:
```bash
chmod +x scripts/deploy.sh
git add robot/picrawler_ctl.py robot/teleop.py scripts/deploy.sh
git commit -m "feat: picrawler_ctl core API, teleop, deploy script"
```

### Task 2.3: Deploy and verify a real walk

- [ ] **Step 1: Deploy**

Run:
```bash
export SSHPASS='W3lcome098!'; export PI="smaniak@172.16.10.117"
./scripts/deploy.sh
```
Expected: `deployed ...`.

- [ ] **Step 2: Servo calibration (guided)**

Run SunFounder's calibration tool so the legs sit correctly:
```bash
ssh_pi "cd ~/sf/picrawler/examples && ls *calibrat* 2>/dev/null"
```
If a calibration example exists (e.g. `calibration/calibration.py`), tell the user to run it interactively over SSH (it requires watching the robot) and follow the on-screen prompts to zero each leg. Record final offsets in `docs/hardware-notes.md`.

- [ ] **Step 3: One-command walk test**

> ⚠️ Tell the user: "Testing a real forward walk now — make sure the robot has clear space."

Run:
```bash
ssh_pi "cd ~/picrawler-app && ./.venv/bin/python -c 'from picrawler_ctl import get_controller as g; print(g().forward(steps=3))'"
```
Expected: printed `{'action': 'forward', 'steps': 3, 'speed': 80}` **and the robot physically walks forward 3 steps.** If it moves wrong/jerky, revisit calibration (Step 2) and `ACTION_MAP` (Task 2.1).

- [ ] **Step 4: Camera + speak sanity**

Run:
```bash
ssh_pi "cd ~/picrawler-app && ./.venv/bin/python -c 'from picrawler_ctl import get_controller as g; c=g(); print(c.photo()); print(c.speak(\"hello, I am online\"))'"
```
Expected: a photo path under `~/picrawler-app/photos/` is printed (verify the file exists), and audible speech. If TTS is silent, check Task 1.4 speaker setup / fall back to USB card 3.

---

# Phase 3 — MCP Server + systemd Service

**Goal:** a remote MCP client drives the robot and reads the camera over HTTP.

### Task 3.1: Write the MCP server

**Files:**
- Create: `robot/mcp_server.py`
- Create: `robot/config.example.env`

- [ ] **Step 1: Write `robot/config.example.env`**

Create `robot/config.example.env`:
```bash
# Copy to ~/picrawler-app/.env on the Pi and fill in. NEVER commit the real .env.
OPENAI_API_KEY=
# MCP auth: leave empty for no-auth (LAN). Set a value to require this bearer token.
MCP_AUTH_TOKEN=
MCP_HOST=0.0.0.0
MCP_PORT=8000
```

- [ ] **Step 2: Write `robot/mcp_server.py`**

Create `robot/mcp_server.py`:
```python
"""FastMCP server exposing PiCrawler control, vision, tuning, and code/deploy.
Transport: streamable-HTTP on MCP_HOST:MCP_PORT, path /mcp.
Auth: none unless MCP_AUTH_TOKEN is set (bearer token). LAN use by default."""
from __future__ import annotations
import os
import base64
import subprocess
from pathlib import Path

from fastmcp import FastMCP
from picrawler_ctl import get_controller

APP_DIR = Path(os.path.expanduser("~/picrawler-app")).resolve()

# Load .env if present (simple parser; avoids extra deps)
_env = APP_DIR / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

mcp = FastMCP("picrawler")

def _safe_path(rel: str) -> Path:
    """Resolve rel under APP_DIR, refusing escapes."""
    p = (APP_DIR / rel).resolve()
    if not str(p).startswith(str(APP_DIR)):
        raise ValueError(f"path {rel!r} escapes app dir")
    return p

# ---- Runtime control -----------------------------------------------------
@mcp.tool()
def forward(steps: int = 1, speed: int | None = None) -> dict:
    """Walk forward `steps` gait cycles."""
    return get_controller().forward(steps, speed)

@mcp.tool()
def backward(steps: int = 1, speed: int | None = None) -> dict:
    """Walk backward `steps` gait cycles."""
    return get_controller().backward(steps, speed)

@mcp.tool()
def turn_left(steps: int = 1, speed: int | None = None) -> dict:
    """Turn left `steps` gait cycles."""
    return get_controller().turn_left(steps, speed)

@mcp.tool()
def turn_right(steps: int = 1, speed: int | None = None) -> dict:
    """Turn right `steps` gait cycles."""
    return get_controller().turn_right(steps, speed)

@mcp.tool()
def stand() -> dict:
    """Stand in the neutral pose."""
    return get_controller().stand()

@mcp.tool()
def rest() -> dict:
    """Sit/rest pose."""
    return get_controller().rest()

@mcp.tool()
def stop() -> dict:
    """Stop and hold a safe standing pose."""
    return get_controller().stop()

@mcp.tool()
def status() -> dict:
    """Return current speed and camera state."""
    return get_controller().status()

@mcp.tool()
def speak(text: str) -> dict:
    """Speak `text` aloud via onboard TTS."""
    return get_controller().speak(text)

# ---- Vision --------------------------------------------------------------
@mcp.tool()
def capture_image() -> dict:
    """Capture a camera frame; returns base64-encoded JPEG."""
    jpg = get_controller().capture_jpeg_bytes()
    return {"mime": "image/jpeg", "base64": base64.b64encode(jpg).decode()}

# ---- Tuning --------------------------------------------------------------
@mcp.tool()
def set_speed(speed: int) -> dict:
    """Set default gait speed (1-100)."""
    return {"speed": get_controller().set_speed(speed)}

# ---- Code + deploy (scoped to ~/picrawler-app) ---------------------------
@mcp.tool()
def list_files(subdir: str = ".") -> dict:
    """List files under the app dir."""
    base = _safe_path(subdir)
    return {"path": str(base),
            "entries": sorted(p.name + ("/" if p.is_dir() else "")
                              for p in base.iterdir())}

@mcp.tool()
def read_file(path: str) -> dict:
    """Read a text file under the app dir."""
    return {"path": path, "content": _safe_path(path).read_text()}

@mcp.tool()
def write_file(path: str, content: str) -> dict:
    """Write a text file under the app dir (creates parent dirs)."""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"path": path, "bytes": len(content)}

@mcp.tool()
def restart_robot_service(service: str = "picrawler-mcp") -> dict:
    """Restart a systemd service (picrawler-mcp or picrawler-web)."""
    if service not in {"picrawler-mcp", "picrawler-web"}:
        raise ValueError("service must be picrawler-mcp or picrawler-web")
    out = subprocess.run(["sudo", "systemctl", "restart", service],
                         capture_output=True, text=True)
    return {"service": service, "rc": out.returncode, "stderr": out.stderr}


def _check_auth():
    """If MCP_AUTH_TOKEN set, install a bearer-token middleware."""
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if token:
        from fastmcp.server.auth import StaticTokenVerifier  # per fastmcp docs
        mcp.auth = StaticTokenVerifier(tokens={token: {"client": "lan"}})

if __name__ == "__main__":
    _check_auth()
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="http", host=host, port=port, path="/mcp")
```

> **Verify during Task 3.1:** the FastMCP HTTP run signature and the auth-verifier
> class name via `mcp__plugin_context7_context7` (resolve `fastmcp`) or the FastMCP
> docs, since these APIs move. Adjust `mcp.run(...)` / `_check_auth()` if needed.

- [ ] **Step 3: Add fastmcp to the venv and deploy**

Run:
```bash
ssh_pi "~/picrawler-app/.venv/bin/pip install 'fastmcp>=2.0'"
./scripts/deploy.sh
ssh_pi "cp -n ~/picrawler-app/config.example.env ~/picrawler-app/.env || true"
```
Expected: fastmcp installed; `.env` created on the Pi (edit later for the OpenAI key).

### Task 3.2: Install the systemd service

**Files:**
- Create: `scripts/picrawler-mcp.service`

- [ ] **Step 1: Write the unit file**

Create `scripts/picrawler-mcp.service`:
```ini
[Unit]
Description=PiCrawler MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=smaniak
WorkingDirectory=/home/smaniak/picrawler-app
ExecStart=/home/smaniak/picrawler-app/.venv/bin/python /home/smaniak/picrawler-app/mcp_server.py
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Allow the service user to restart services without a password**

Run (needed for the `restart_robot_service` MCP tool):
```bash
ssh_sudo "bash -c 'echo \"smaniak ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart picrawler-mcp, /usr/bin/systemctl restart picrawler-web\" > /etc/sudoers.d/picrawler && chmod 440 /etc/sudoers.d/picrawler'"
ssh_pi "sudo -n systemctl is-active picrawler-mcp; echo rc=$?"
```
Expected: no password prompt (rc line prints; service not yet installed is fine).

- [ ] **Step 3: Install, enable, and start the service**

Run:
```bash
sshpass -e scp -o StrictHostKeyChecking=no scripts/picrawler-mcp.service "$PI:/tmp/"
ssh_sudo "mv /tmp/picrawler-mcp.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now picrawler-mcp"
ssh_pi "systemctl status picrawler-mcp --no-pager | head -8"
```
Expected: `active (running)`.

- [ ] **Step 4: Verify the HTTP endpoint responds**

Run:
```bash
ssh_pi "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/mcp"
```
Expected: an HTTP status (e.g. `200`/`400`/`406` depending on handshake) — a numeric code proves the server is listening. Also from the laptop:
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://172.16.10.117:8000/mcp
```
Expected: same — proves LAN reachability.

- [ ] **Step 5: Commit**

```bash
git add robot/mcp_server.py robot/config.example.env scripts/picrawler-mcp.service
git commit -m "feat: FastMCP server + systemd service for PiCrawler"
```

### Task 3.3: Connect from Claude Code and drive the robot via MCP

- [ ] **Step 1: Register the MCP server**

Run:
```bash
claude mcp add --transport http picrawler http://172.16.10.117:8000/mcp
claude mcp list
```
Expected: `picrawler` listed and reachable.

- [ ] **Step 2: End-to-end tool call**

In this session, call the `forward` tool (steps=2) and the `capture_image` tool.
Expected: robot walks; `capture_image` returns base64 JPEG. **This is the Phase 3 acceptance test.**

---

# Phase 4 — Claude Code Skill

**Goal:** a `picrawler-control` skill documenting how to operate the robot.

### Task 4.1: Create the skill

**Files:**
- Create: `~/.claude/skills/picrawler-control/SKILL.md`
- Create (repo copy): `robot/skill/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `~/.claude/skills/picrawler-control/SKILL.md`:
```markdown
---
name: picrawler-control
description: Use when the user wants to operate, drive, move, or interact with the SunFounder PiCrawler robot (walk/turn/stand/rest, take a photo, speak, check status, tune speed, or reprogram it). Controls the robot at 172.16.10.117 via its MCP server (preferred) or SSH.
---

# PiCrawler Control

Operate the SunFounder PiCrawler (Raspberry Pi 4 at `172.16.10.117`).

## Preferred: MCP server
The robot runs a FastMCP server at `http://172.16.10.117:8000/mcp`. If the
`picrawler` MCP server is connected, use its tools directly:
- Movement: `forward`, `backward`, `turn_left`, `turn_right`, `stand`, `rest`, `stop` (all take `steps`, `speed`).
- `status`, `set_speed`, `speak(text)`.
- Vision: `capture_image()` → base64 JPEG (decode to see what the robot sees).
- Code/deploy: `list_files`, `read_file`, `write_file`, `restart_robot_service`.

If not connected: `claude mcp add --transport http picrawler http://172.16.10.117:8000/mcp`

## Safety
- Before movement, confirm the robot has clear space (ask the user if unsure).
- Use small `steps` (1-3) first. Keep `speed` ≤ 80 unless asked.
- After a movement sequence, call `stop` to leave it in a safe stand.

## SSH fallback (if MCP is down)
```bash
export SSHPASS='W3lcome098!'
sshpass -e ssh -o StrictHostKeyChecking=no smaniak@172.16.10.117 \
  "cd ~/picrawler-app && ./.venv/bin/python -c 'from picrawler_ctl import get_controller as g; print(g().forward(2))'"
```

## Service management
- Status: `sshpass -e ssh ... "systemctl status picrawler-mcp --no-pager"`
- Restart: `restart_robot_service` MCP tool, or `sudo systemctl restart picrawler-mcp`.
- Logs: `journalctl -u picrawler-mcp -n 50 --no-pager`

## Troubleshooting
- No movement / import errors: check I2C (`i2cdetect -y 1` shows the HAT) and that the battery is on.
- Camera errors: the venv must be `--system-site-packages` (sees system picamera2).
- TTS silent: verify i2samp speaker setup or fall back to USB sound card.
```

- [ ] **Step 2: Copy into the repo and commit**

Run:
```bash
mkdir -p robot/skill && cp ~/.claude/skills/picrawler-control/SKILL.md robot/skill/SKILL.md
git add robot/skill/SKILL.md && git commit -m "feat: picrawler-control Claude Code skill"
```

- [ ] **Step 3: Verify the skill loads**

Restart/refresh skills and confirm `picrawler-control` appears in the available skills list. Acceptance: invoking it and issuing "walk forward and take a photo" drives the robot via MCP.

---

# Phase 5 — Web Control Panel

**Goal:** drive the robot and see a live camera feed from a browser on the LAN.

### Task 5.1: Write the Flask panel

**Files:**
- Create: `robot/web/app.py`
- Create: `robot/web/templates/index.html`

- [ ] **Step 1: Write `robot/web/app.py`**

Create `robot/web/app.py`:
```python
"""Flask control panel + MJPEG stream. Serves on :5000.
Run: ~/picrawler-app/.venv/bin/python ~/picrawler-app/web/app.py"""
import sys, os, time
sys.path.insert(0, os.path.expanduser("~/picrawler-app"))  # find picrawler_ctl
from flask import Flask, render_template, jsonify, Response, request
from picrawler_ctl import get_controller

app = Flask(__name__)
c = get_controller()

ACTIONS = {"forward": c.forward, "backward": c.backward,
           "turn_left": c.turn_left, "turn_right": c.turn_right,
           "stand": c.stand, "rest": c.rest, "stop": c.stop}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/<action>", methods=["POST"])
def do(action):
    fn = ACTIONS.get(action)
    if not fn:
        return jsonify(error="unknown action"), 400
    steps = int(request.json.get("steps", 1)) if request.is_json else 1
    try:
        return jsonify(fn(steps) if action in
                       ("forward","backward","turn_left","turn_right") else fn())
    except TypeError:
        return jsonify(fn())

@app.route("/api/speed", methods=["POST"])
def speed():
    return jsonify(speed=c.set_speed(int(request.json["speed"])))

def _mjpeg():
    while True:
        try:
            frame = c.capture_jpeg_bytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        except Exception:
            time.sleep(0.2)
        time.sleep(0.05)

@app.route("/stream")
def stream():
    return Response(_mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
```

- [ ] **Step 2: Write `robot/web/templates/index.html`**

Create `robot/web/templates/index.html`:
```html
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PiCrawler Control</title>
<style>
 body{font-family:system-ui;text-align:center;background:#111;color:#eee;margin:0;padding:1rem}
 img{max-width:100%;border-radius:8px;background:#000}
 .pad{display:grid;grid-template-columns:repeat(3,80px);gap:8px;justify-content:center;margin:1rem auto}
 button{font-size:1.4rem;padding:14px;border:0;border-radius:10px;background:#2b6;color:#000}
 button:active{background:#184}
 .b2{background:#c73;color:#fff}
 input[type=range]{width:80%}
</style></head><body>
<h2>PiCrawler</h2>
<img src="/stream" alt="camera">
<div class="pad">
 <span></span><button onclick="go('forward')">▲</button><span></span>
 <button onclick="go('turn_left')">◀</button>
 <button class="b2" onclick="go('stop')">■</button>
 <button onclick="go('turn_right')">▶</button>
 <span></span><button onclick="go('backward')">▼</button><span></span>
</div>
<button onclick="go('stand')">Stand</button>
<button onclick="go('rest')">Rest</button>
<p>Speed <input type="range" min="1" max="100" value="80"
   onchange="setSpeed(this.value)"></p>
<script>
 async function go(a){await fetch('/api/'+a,{method:'POST',
   headers:{'Content-Type':'application/json'},body:JSON.stringify({steps:2})});}
 async function setSpeed(v){await fetch('/api/speed',{method:'POST',
   headers:{'Content-Type':'application/json'},body:JSON.stringify({speed:+v})});}
</script></body></html>
```

- [ ] **Step 3: Deploy and run once (foreground) to verify**

Run:
```bash
ssh_pi "~/picrawler-app/.venv/bin/pip install 'flask>=3.0' opencv-python-headless"
./scripts/deploy.sh
ssh_pi "cd ~/picrawler-app && (./.venv/bin/python web/app.py &) ; sleep 3; curl -s -o /dev/null -w 'web:%{http_code}\n' http://127.0.0.1:5000/"
```
Expected: `web:200`. Then open `http://172.16.10.117:5000` in a laptop/phone browser: you should see the camera stream and the D-pad drives the robot. Stop the foreground test: `ssh_pi "pkill -f web/app.py"`.

### Task 5.2: Install web panel as a service

- [ ] **Step 1: Create and install `picrawler-web.service`**

Create `scripts/picrawler-web.service` (same shape as the MCP unit, different ExecStart):
```ini
[Unit]
Description=PiCrawler web panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=smaniak
WorkingDirectory=/home/smaniak/picrawler-app
ExecStart=/home/smaniak/picrawler-app/.venv/bin/python /home/smaniak/picrawler-app/web/app.py
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
Install:
```bash
sshpass -e scp -o StrictHostKeyChecking=no scripts/picrawler-web.service "$PI:/tmp/"
ssh_sudo "mv /tmp/picrawler-web.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now picrawler-web"
ssh_pi "systemctl is-active picrawler-web"
```
Expected: `active`.

> **Note — single camera owner:** `vilib`/the camera can be opened by only one
> process. The MCP server, web panel, and AI assistant each call
> `capture_jpeg_bytes()`, so do NOT run camera-using services simultaneously
> unless verified. If both MCP and web need the camera at once, refactor to a
> single camera-owning process later; for now, run one camera consumer at a time
> (document this in `docs/hardware-notes.md`).

- [ ] **Step 2: Commit**

```bash
git add robot/web/app.py robot/web/templates/index.html scripts/picrawler-web.service
git commit -m "feat: web control panel + MJPEG stream + service"
```

---

# Phase 6 — OpenAI Voice+Video Assistant

**Goal:** speak to the robot; it transcribes, reasons over speech + a camera frame, replies aloud, and can move.

**Prerequisite:** user provides the OpenAI API key (written to `~/picrawler-app/.env`).

### Task 6.1: Write the assistant loop

**Files:**
- Create: `robot/ai_assistant.py`

- [ ] **Step 1: Put the API key on the Pi (do NOT commit)**

Run (ask the user for the key first):
```bash
ssh_pi "sed -i 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY=THE_KEY_HERE|' ~/picrawler-app/.env && grep -c OPENAI_API_KEY ~/picrawler-app/.env"
```
Expected: `1`. Confirm `.env` is git-ignored (it is never synced by deploy.sh).

- [ ] **Step 2: Write `robot/ai_assistant.py`**

Create `robot/ai_assistant.py`:
```python
"""OpenAI voice+video assistant for PiCrawler.
Loop: record mic -> Whisper STT -> GPT (with camera frame) -> TTS reply + move.
Run: ~/picrawler-app/.venv/bin/python ~/picrawler-app/ai_assistant.py"""
import os, sys, json, base64, tempfile, wave
sys.path.insert(0, os.path.expanduser("~/picrawler-app"))
import numpy as np
import sounddevice as sd
from openai import OpenAI
from picrawler_ctl import get_controller

# load .env
envp = os.path.expanduser("~/picrawler-app/.env")
for line in open(envp):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

client = OpenAI()  # reads OPENAI_API_KEY
c = get_controller()
SAMPLE_RATE = 16000
RECORD_SECONDS = 5

TOOLS = [{
    "type": "function",
    "function": {
        "name": "move",
        "description": "Move the robot.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["forward","backward","turn_left","turn_right","stand","rest","stop"]},
            "steps": {"type": "integer", "default": 2}},
            "required": ["action"]}}}]

def record_wav() -> str:
    print("listening...")
    audio = sd.rec(int(RECORD_SECONDS*SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="int16"); sd.wait()
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.tobytes())
    return path

def transcribe(path: str) -> str:
    with open(path, "rb") as f:
        return client.audio.transcriptions.create(
            model="whisper-1", file=f).text

def see() -> str:
    return base64.b64encode(c.capture_jpeg_bytes()).decode()

def reply(text: str, img_b64: str):
    msgs = [{"role": "system", "content":
             "You are a friendly quadruped robot. Keep replies short. "
             "Use the move tool when asked to move."},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]
    r = client.chat.completions.create(model="gpt-4o", messages=msgs,
                                       tools=TOOLS, max_tokens=200)
    m = r.choices[0].message
    if m.tool_calls:
        for tc in m.tool_calls:
            args = json.loads(tc.function.arguments)
            getattr(c, args["action"])(*( [args.get("steps",2)]
                     if args["action"] in ("forward","backward","turn_left","turn_right")
                     else []))
    return m.content or "Okay."

def speak(text: str):
    out = tempfile.mktemp(suffix=".mp3")
    with client.audio.speech.with_streaming_response.create(
            model="tts-1", voice="alloy", input=text) as resp:
        resp.stream_to_file(out)
    os.system(f"ffplay -nodisp -autoexit -loglevel quiet {out}")

def main():
    print("assistant ready — Ctrl-C to quit")
    while True:
        wav = record_wav()
        text = transcribe(wav).strip()
        if not text:
            continue
        print("you:", text)
        answer = reply(text, see())
        print("bot:", answer)
        speak(answer)

if __name__ == "__main__":
    main()
```

> **Verify during Task 6.1:** the OpenAI SDK method names (`audio.transcriptions`,
> `chat.completions`, `audio.speech.with_streaming_response`) against the current
> SDK via context7 (`openai-python`) or the claude-api/OpenAI docs — pin as needed.

- [ ] **Step 3: Install deps, deploy, and dry-run STT+TTS (no movement)**

Run:
```bash
ssh_pi "~/picrawler-app/.venv/bin/pip install 'openai>=1.30' sounddevice numpy"
./scripts/deploy.sh
ssh_pi "cd ~/picrawler-app && ./.venv/bin/python -c 'import ai_assistant as a; a.speak(\"assistant online\")'"
```
Expected: audible "assistant online". If `ffplay` missing, install `ffmpeg` (done in Task 1.2).

- [ ] **Step 4: Full interactive test**

Tell the user to run on the Pi (needs mic + speaker + clear space):
```bash
ssh_pi -t "cd ~/picrawler-app && ./.venv/bin/python ai_assistant.py"
```
Say "walk forward two steps and tell me what you see." Expected: transcribed, robot moves, spoken reply describing the camera view. **This is the Phase 6 acceptance test.**

- [ ] **Step 5: Commit**

```bash
git add robot/ai_assistant.py && git commit -m "feat: OpenAI voice+video assistant loop"
```

---

## Final verification checklist

- [ ] `i2cdetect -y 1` shows the Robot HAT (Phase 0).
- [ ] `import robot_hat, vilib, picrawler` OK in the venv (Phase 1).
- [ ] One-command `forward` walks the robot; `photo()` + `speak()` work (Phase 2).
- [ ] `picrawler-mcp` service `active`; MCP `forward`/`capture_image` work from Claude Code (Phase 3).
- [ ] `picrawler-control` skill loads and drives the robot (Phase 4).
- [ ] Web panel at `:5000` streams camera and drives robot (Phase 5).
- [ ] Voice conversation moves the robot and describes the view (Phase 6).
- [ ] `.env` never committed; `git status` clean; all tasks committed.
```
