---
name: picrawler-control
description: Use when the user wants to operate, drive, move, or interact with the SunFounder PiCrawler spider robot (walk/turn/stand/rest/sit, poses like wave/dance/push_up/look, take a photo, speak, check battery/status, tune speed, calibrate servos, or reprogram it). Controls the robot on the Raspberry Pi at 172.16.10.117 via its MCP server (preferred) or SSH.
---

# PiCrawler Control

Operate the SunFounder PiCrawler (8-servo quadruped, no head servo) running on a
Raspberry Pi 4 at `172.16.10.117` (user `smaniak`, SSH alias `pi-crawler`).

## Preferred: MCP server
The robot runs a FastMCP server at `http://172.16.10.117:8000/mcp`. If the
`picrawler` MCP server is connected, use its tools directly:

- **Move:** `forward`, `backward`, `turn_left`, `turn_right` (args: `steps`, `speed`);
  `stand`, `rest`, `stop`.
- **Poses:** `pose(name)` — `wave`, `push_up`, `dance`, `look_up`, `look_down`,
  `look_left`, `look_right`, `ready` (look_* tilt the whole body; there is no head servo).
- **Vision:** `capture_image()` → returns a JPEG you can view.
- **Voice:** `speak(text)`.
- **State:** `status()`, `battery()`.
- **Tuning:** `set_speed(1-100)`, `calibrate_leg(index 0-11, offset ±20)`,
  `get_offsets()`, `set_battery_guard(bool)`.
- **Code/deploy:** `list_files`, `read_file`, `write_file`, `restart_robot_service`
  (all scoped to `~/picrawler-app/`).

Connect if not already: `claude mcp add --transport http picrawler http://172.16.10.117:8000/mcp`

## ⚠️ Safety — battery brownout
The robot browns out (and the Pi loses power) if the battery is weak. ALWAYS:
1. Check `battery()` / `status()` first. If `battery_v` < ~6.8V, tell the user to
   charge — the built-in guard will refuse to move and raise a low-battery error.
2. Confirm the robot has **clear space** before moving (ask the user if unsure).
3. Start with small `steps` (1–2) and `speed` ≤ 80.
4. Call `stop` after a movement sequence to leave it in a safe stand.

## Only one hardware owner at a time
The MCP server, web panel (`:5000`), and AI assistant each take exclusive control
of the HAT + camera. Only ONE may run at once. `picrawler-mcp` is the default
autostart service. To use the web panel or assistant, stop the MCP service first:
`sudo systemctl stop picrawler-mcp` (and restart it after).

## SSH fallback (if MCP is down)
```bash
ssh pi-crawler "cd ~/picrawler-app && ./.venv/bin/python -c \
  'from picrawler_ctl import get_controller as g; print(g().status())'"
# move:
ssh pi-crawler "cd ~/picrawler-app && ./.venv/bin/python -c \
  'from picrawler_ctl import get_controller as g; print(g().forward(2))'"
```
(The `pi-crawler` SSH alias uses key auth; see ~/.ssh/config.)

## Service management
- Status: `ssh pi-crawler "systemctl status picrawler-mcp --no-pager"`
- Logs:   `ssh pi-crawler "journalctl -u picrawler-mcp -n 50 --no-pager"`
- Restart: MCP `restart_robot_service` tool, or `sudo systemctl restart picrawler-mcp`

## Troubleshooting
- **No movement / brownout / Pi unreachable after a move** → battery too low.
  Charge the 2S 18650 pack; confirm HAT power switch on.
- **No I2C / import errors** → `ssh pi-crawler "sudo i2cdetect -y 1"` should show
  the HAT at `0x14`.
- **Camera errors** → the app venv must be `--system-site-packages` (system picamera2).
- **TTS silent** → onboard speaker is `hifiberry-dac` (card 3); check `/etc/asound.conf`.
- **Local ML vision (face/pose) missing** → mediapipe is unsupported on this Pi's
  Python 3.13; the AI assistant uses OpenAI cloud vision instead.

## Deploying code changes (from the dev repo)
Code lives in the `spiderman` repo under `robot/`. Deploy with:
`SSHPASS=... ./scripts/deploy.sh` (or just `./scripts/deploy.sh` with key auth),
then `restart_robot_service`.
