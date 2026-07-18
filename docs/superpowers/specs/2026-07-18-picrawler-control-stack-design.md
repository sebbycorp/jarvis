# PiCrawler Control Stack — Design

**Date:** 2026-07-18
**Author:** Sebastian Maniak (with Claude)
**Status:** Approved

## Goal

Get a SunFounder PiCrawler AI Robot Kit (running on a Raspberry Pi 4) fully
operational, and build a layered control stack so the robot can be driven from:

1. A **Claude Code skill** (operate from the laptop).
2. A **web control panel** (browser/phone, with live camera).
3. An **AI voice+video assistant** (OpenAI/ChatGPT running on the Pi).
4. An **MCP server on the Pi** (primary remote interface — any MCP client/agent
   connects over HTTP and controls, tunes, reprograms, and sees through the robot).

## Target Environment (assessed 2026-07-18)

- **Host:** Raspberry Pi 4, reachable at `172.16.10.117` (user `smaniak`).
- **OS:** Debian 13 "Trixie", 64-bit (`aarch64`).
- **Python:** 3.13.5 (PEP 668 externally-managed).
- **PiCrawler software:** not installed (`robot-hat`, `vilib`, `picrawler` absent).
- **I2C:** NOT enabled for the GPIO header — `dtparam=i2c_arm=on` is commented out
  in `/boot/firmware/config.txt` and `/dev/i2c-1` is missing. The Robot HAT
  communicates over `i2c-1`, so this must be enabled (requires reboot).
- **Camera:** present (`/dev/video0`, `rpicam-hello`).
- **Audio:** USB PnP Sound Device (card 3) present — used as the microphone.

## Architecture

Bottom-up, four layers. Everything funnels through one small module we write.

```
┌──────────────────────────────────────────────────────────────┐
│  Interfaces                                                    │
│   • MCP server (PRIMARY) — FastMCP, streamable-HTTP, systemd   │
│       control · vision · tuning · code+deploy                  │
│   • Web control panel — Flask + MJPEG camera stream           │
│   • AI voice+video assistant — OpenAI GPT + Whisper STT + TTS │
│   • Claude Code skill — documents how to connect & operate     │
├──────────────────────────────────────────────────────────────┤
│  picrawler_ctl.py — one clean, safe API                       │
│   forward/backward/turn/stand/rest/move_head/photo/speak/stop │
├──────────────────────────────────────────────────────────────┤
│  SunFounder stack — robot-hat, vilib, picrawler               │
├──────────────────────────────────────────────────────────────┤
│  Hardware — I2C enabled, servos calibrated, camera, mic       │
└──────────────────────────────────────────────────────────────┘
```

**Core idea:** all four interfaces are thin front-ends over a single Python
module, `picrawler_ctl.py`, which wraps the SunFounder `picrawler` library into
simple, safe commands. Build and test the core once; reuse it four times.

## Components

### `picrawler_ctl.py` (the foundation API)
- Wraps `picrawler.Picrawler`, `robot_hat`, and `vilib`.
- Exposes: `forward(steps, speed)`, `backward(...)`, `turn_left(...)`,
  `turn_right(...)`, `stand()`, `rest()`, `move_head(pan, tilt)`, `stop()`,
  `photo() -> path`, `speak(text)`, `status() -> dict`.
- Owns safety: clamps speed/angles, guarantees `stop()` leaves servos in a safe
  pose, single point of hardware access (avoids two callers fighting the HAT).

### MCP server (`robot/mcp_server.py`) — primary remote interface
- Framework: **FastMCP**, transport **streamable-HTTP**, bound to `0.0.0.0:8000`,
  path `/mcp`.
- Runs as a **systemd service** (starts on boot, restarts on crash).
- Auth: **none** on the LAN by default (trusted lab network). A bearer-token
  check is included but disabled via a one-line config flag, so it can be turned
  on later without a rebuild.
- Tool groups:
  - **Runtime control:** `forward`, `backward`, `turn_left`, `turn_right`,
    `stand`, `rest`, `move_head`, `stop`, `status`.
  - **Vision:** `capture_image()` → base64 JPEG.
  - **Tuning:** `set_speed`, `calibrate_leg(leg, offset)`, `save_pose(name)`,
    `run_pose(name)`, `list_poses`.
  - **Code + deploy:** `list_files`, `read_file`, `write_file`,
    `restart_robot_service` — all scoped to `~/picrawler-app/`.
- Client connect (Claude Code):
  `claude mcp add --transport http picrawler http://172.16.10.117:8000/mcp`

### Web control panel (`robot/web/`)
- Flask app on `:5000`. D-pad style movement buttons, speed slider, pose buttons,
  and a live **MJPEG** camera stream. Reachable from laptop/phone on the LAN.

### AI voice+video assistant (`robot/ai_assistant.py`)
- OpenAI (`gpt-4o`) for reasoning, Whisper for STT, OpenAI TTS for speech.
- Loop: listen (USB mic) → transcribe → send transcript + camera frame → get
  action + reply → drive robot via `picrawler_ctl` + speak reply.
- API key read from `~/picrawler-app/.env` (never committed).

### Claude Code skill (`~/.claude/skills/picrawler-control/`)
- Documents how to connect to the MCP server and operate the robot, plus SSH
  fallbacks and troubleshooting (I2C, service restart, calibration).

## Repository Layout

Code is developed in this git repo and deployed to the Pi over SSH.

```
spiderman/
├── robot/                    # deployed to Pi at ~/picrawler-app/
│   ├── picrawler_ctl.py      # the shared core API
│   ├── mcp_server.py         # FastMCP server (all tool groups)
│   ├── teleop.py             # keyboard drive (manual test tool)
│   ├── web/                  # Flask web panel + MJPEG stream
│   ├── ai_assistant.py       # OpenAI voice+video loop
│   ├── requirements.txt
│   └── config.example.env    # API key template (.env NOT committed)
├── scripts/
│   ├── setup_pi.sh           # enable I2C, install SunFounder stack + deps
│   ├── deploy.sh             # rsync repo → Pi (excludes .env)
│   └── picrawler-mcp.service # systemd unit for the MCP server
└── docs/superpowers/specs/   # this design doc
```

## Key Technical Decisions

- **Python venv with `--system-site-packages`.** SunFounder's `vilib` depends on
  the apt-installed `picamera2`, which a plain venv cannot see. A venv created
  with `--system-site-packages` gives an isolated install that can still reach
  the system camera stack, and sidesteps the PEP 668 restriction cleanly.
- **Install SunFounder libs from GitHub source**, not the pinned installer — the
  source install is more tolerant of the newer Trixie/Python 3.13 environment.
- **API key** lives in `~/picrawler-app/.env` on the Pi, excluded from git and
  from the deploy sync.
- **No agent runs on the Pi for the skill** — the skill and this laptop talk to
  the robot via the MCP server (or SSH fallback).

## Build Phases

Each phase ends in something observable.

| Phase | What | Verify by |
|---|---|---|
| 0 | Enable I2C (`dtparam=i2c_arm=on`), reboot, confirm Robot HAT on `i2c-1` | `i2cdetect -y 1` shows HAT (≈0x14/0x40) |
| 1 | Install SunFounder `robot-hat` + `vilib` + `picrawler` (venv, Trixie workarounds) | import test passes |
| 2 | Calibrate servos + write `picrawler_ctl.py` + test a walk | robot walks forward via one command |
| 3 | MCP server (all 4 tool groups) + systemd service | remote MCP client drives + reads camera |
| 4 | Claude Code skill (connect to MCP + operational docs) | "walk forward, take a photo" works from laptop |
| 5 | Web control panel + live camera | drive from phone at `http://172.16.10.117:5000` |
| 6 | OpenAI voice+video assistant | talk to it, it responds + reacts |

## Risks & Mitigations

- **Trixie / Python 3.13 vs SunFounder's supported Bookworm / 3.11 (primary risk).**
  Install scripts may fail on pinned dependencies. *Mitigation:* install from
  GitHub source in a `--system-site-packages` venv and adapt. If genuinely
  incompatible, fallback is reflashing the SD card to Raspberry Pi OS Bookworm —
  flagged clearly before any drastic action; user's call.
- **Unauthenticated MCP with code+deploy on the LAN.** Anyone on the network can
  run code on the Pi. Accepted for a trusted lab LAN. *Mitigation:* bearer-token
  auth is built in and enabled with a one-line config change.
- **Servo damage from bad calibration/commands.** *Mitigation:* `picrawler_ctl`
  clamps ranges and centralizes hardware access; calibration is an explicit,
  guided step in Phase 2.

## Out of Scope (for now)

- Non-OpenAI LLM providers (Gemini/Grok/Claude) for the assistant.
- Internet-exposed / cloud access to the MCP server (LAN only).
- Scratch programming environment.
