# 🕷️ PiCrawler Control Stack

Control software for a **SunFounder PiCrawler** (8-servo quadruped spider robot)
running on a **Raspberry Pi 4** at `172.16.10.117`. One shared control core drives
four interfaces: an **MCP server** (remote agents), a **Claude Code skill**, a
**web panel**, and an **OpenAI voice+video assistant**.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Interfaces                                                    │
│   • MCP server (primary)  — FastMCP v3, HTTP :8000, systemd    │
│   • Web panel             — Flask :5000 + MJPEG camera stream  │
│   • AI assistant          — OpenAI Whisper + GPT-4o + TTS      │
│   • Claude Code skill     — picrawler-control                  │
├──────────────────────────────────────────────────────────────┤
│  picrawler_ctl.py  — one clean, safe API (battery guard,       │
│                       thread-safe, single HW owner)            │
├──────────────────────────────────────────────────────────────┤
│  SunFounder stack — robot-hat 2.5.x · vilib 0.3.18 · picrawler │
├──────────────────────────────────────────────────────────────┤
│  Hardware — I2C (HAT @0x14) · 8 servos · camera · speaker/mic  │
└──────────────────────────────────────────────────────────────┘
```

**Every interface is a thin front-end over `robot/picrawler_ctl.py`.** Build/test
the core once, reuse it four times.

> ⚠️ **Only ONE interface may run at a time** — the MCP server, web panel, and AI
> assistant each take exclusive ownership of the Robot HAT and camera. `picrawler-mcp`
> is the default autostart service; stop it before running the web panel or assistant
> (`make stop-all`).

## Repository layout

```
robot/                      # deployed to Pi:~/picrawler-app/
├── picrawler_ctl.py        # shared core API + battery guard
├── mcp_server.py           # FastMCP server (control/vision/tuning/code+deploy)
├── web/app.py + templates/ # Flask panel + MJPEG stream
├── ai_assistant.py         # OpenAI voice+video loop
├── teleop.py               # keyboard drive (manual test)
├── preflight.py            # health-check (run before operating)
├── requirements.txt        # our pip deps (SunFounder libs are system-wide)
└── config.example.env      # copy to .env on the Pi (holds OpenAI key etc.)
scripts/
├── deploy.sh               # rsync robot/ -> Pi (excludes .env)
└── picrawler-{mcp,web}.service   # systemd units
docs/
├── hardware-notes.md       # live device facts, versions, caveats
└── superpowers/{specs,plans}/    # design + implementation plan
Makefile                    # operational shortcuts (see `make help`)
```

## Hardware facts (this device)

- **OS:** Debian 13 "Trixie", 64-bit, **Python 3.13** (newer than SunFounder's
  supported Bookworm — see caveats).
- **Robot HAT** on I2C bus 1 at **`0x14`**. 8 servos (4 legs × 2), **no head servo**.
- **Camera:** `/dev/video0` (picamera2 0.3.36). **Speaker:** onboard hifiberry DAC.
- **Mic:** USB PnP Sound Device.

### Trixie / Python 3.13 caveats
- vilib's **local ML recognition** (face/hand/pose via mediapipe) is unavailable —
  mediapipe/tflite don't support Python 3.13. Basic camera capture works; the AI
  assistant "sees" via OpenAI cloud vision instead.
- SunFounder libs are installed **system-wide** via their official installers; the
  app venv is created with `--system-site-packages` to inherit them.

## Setup (from scratch)

Prereqs on the laptop: `ssh`, `rsync`; the `pi-crawler` SSH alias (key auth) in
`~/.ssh/config`. Then on the Pi (one time):

1. **Enable I2C:** `dtparam=i2c_arm=on` in `/boot/firmware/config.txt`, reboot.
2. **Install SunFounder stack** (system-wide):
   ```bash
   cd ~/sf
   git clone -b 2.5.x --depth 1 https://github.com/sunfounder/robot-hat && \
     cd robot-hat && sudo python3 install.py && sudo bash i2samp.sh
   git clone --depth 1 https://github.com/sunfounder/vilib && \
     cd ../vilib && sudo python3 install.py
   git clone --depth 1 https://github.com/sunfounder/picrawler && \
     sudo pip3 install ~/sf/picrawler --break-system-packages
   ```
3. **App venv + our deps:**
   ```bash
   python3 -m venv --system-site-packages ~/picrawler-app/.venv
   ~/picrawler-app/.venv/bin/pip install -r ~/picrawler-app/requirements.txt
   ```
4. **Deploy + services:** `make deploy && make install-services`
5. **Config:** `cp ~/picrawler-app/config.example.env ~/picrawler-app/.env` and edit.

## Operating

```bash
make help          # list all shortcuts
make preflight     # health-check: I2C, battery, camera, imports, service
make deploy        # push code changes to the Pi
make walk          # quick 2-step forward test (respects battery guard)
make status        # controller status + battery voltage
make logs          # tail MCP service logs
make restart       # restart MCP service
make stop-all      # free the HAT/camera for web/ai
```

### Via MCP (Claude Code / other agents)
```bash
make mcp-add       # claude mcp add --transport http picrawler http://172.16.10.117:8000/mcp
```
Tools: `forward/backward/turn_left/turn_right/stand/rest/stop`, `pose(name)`,
`capture_image`, `speak`, `status`, `battery`, `set_speed`, `calibrate_leg`,
`list_files/read_file/write_file`, `restart_robot_service`.

### Via web panel
`make stop-all && make web`, then open `http://172.16.10.117:5000` on a phone/laptop.

### Via AI assistant
Add `OPENAI_API_KEY` to `~/picrawler-app/.env`, then `make ai` and talk to it.

## ⚠️ Battery safety

The robot **browns out and the Pi loses power** if the battery is weak — this is the
#1 operational hazard. `picrawler_ctl` includes a **voltage guard** that refuses to
move below `PICRAWLER_MIN_BATTERY_V` (default **6.8V**, 2S pack). Always run
`make preflight` (or check `battery()`) before operating, and keep the 2× 18650
cells charged. Override the guard only if you know what you're doing:
`set_battery_guard(False)`.

## Security notes

The MCP server is **unauthenticated on the LAN by default** (trusted-lab choice) and
exposes code read/write + service restart. To require a token, set `MCP_AUTH_TOKEN`
in `.env` (clients then send `Authorization: Bearer <token>`). Inputs are sanitized
against path traversal (photo names) and shell injection (`speak` text). The real
`.env` (with your API key) is git-ignored and never synced by `deploy.sh`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Pi unreachable right after a move | Battery brownout — charge the pack, power-cycle |
| `make walk` errors "battery … < minimum" | Guard working as intended — charge |
| No movement / import errors | Check `sudo i2cdetect -y 1` shows HAT at `0x14` |
| Camera errors | venv must be `--system-site-packages` (system picamera2) |
| TTS silent | Onboard speaker = hifiberry DAC; check `/etc/asound.conf` |
| Two interfaces fighting | Only one may own the HAT/camera — `make stop-all` first |

See `docs/hardware-notes.md` for the full device log and `docs/superpowers/` for the
design spec and implementation plan.
