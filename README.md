# 🔊 Voice Box

An always-on AI voice assistant running on a **Raspberry Pi 4** at `172.16.10.117`.
Speech stays on the device; thinking goes to **AgentGateway**, which fronts three
model backends you can switch between by voice.

> **History:** this was a SunFounder PiCrawler spider robot. The legs broke, so the
> robotics layer is gone (it's in git history if the drone rebuild ever happens).
> The Pi, Robot HAT, speaker, mic and camera stayed — that's a voice box.

## How it works

```
   ┌──────────── on the Pi (no cloud, no API key) ────────────┐
   │  mic → openWakeWord → webrtcvad → whisper.cpp (STT)      │
   │                          ↓                                │
   │                    intent router                          │
   │              ┌───────────┴───────────┐                    │
   │        local intents            everything else           │
   │      (music, volume)                  │                   │
   │                                       ▼                   │
   └───────────────────────────────────────┼───────────────────┘
                                           │  /chat/completions
                    ┌──────────────────────▼──────────────────┐
                    │  AgentGateway @ 172.16.10.155           │
                    │   :31944 /spark  → Qwen3.6-35B (local)  │  ← default
                    │   :30160 /openai → GPT-5.5              │
                    │   :31397 /grok   → grok-4.5             │
                    └──────────────────────┬──────────────────┘
                                           ▼
                          piper (TTS) → aplay → speaker
```

**Speech never leaves the Pi.** The gateway proxies `/chat/completions` only — no
`/audio/*` routes — so STT and TTS are local by necessity, and by preference: no
key on the device, no per-turn cost, and the box still hears you if WAN drops.

## Talking to it

Say the wake word (**"hey Jarvis"** by default), then talk.

| You say | What happens |
|---|---|
| "what's the capital of Peru" | Answered by the default backend (local Qwen) |
| "**ask Grok** why the sky is blue" | That one turn routes to Grok |
| "**hey GPT**, write me a haiku" | That one turn routes to GPT-5.5 |
| "**switch to Grok**" | Changes the default until told otherwise |
| "what do you **see**?" | Grabs a camera frame, sends it to a vision-capable backend |
| "**play** dark side of the moon" | Local library playback — never hits a model |
| "volume up" / "set volume to 40" | ALSA mixer |
| "next track" / "stop the music" | Playback control |
| "forget our conversation" | Clears the rolling history |

Local intents (music, volume, reset) are matched on-device, so they're instant and
work with the gateway down.

## Layout

```
voicebox/                   # deployed to Pi:~/voicebox-app/
├── assistant.py            # the always-on loop + intent routing
├── config.py               # all settings, read from .env
├── llm.py                  # gateway router + voice-driven backend switching
├── audio.py                # shared mic stream, speaker, HAT amp enable
├── wake.py                 # openWakeWord + VAD utterance capture
├── stt.py                  # whisper.cpp (resident model)
├── tts.py                  # piper, with espeak fallback
├── camera.py               # picamera2 stills
├── music.py                # local library playback
├── mcp_server.py           # FastMCP server for remote agents
├── web/                    # Flask control panel
└── preflight.py            # health check
scripts/
├── setup_pi.sh             # idempotent bootstrap (reflash insurance)
├── deploy.sh               # rsync voicebox/ -> Pi (excludes .env)
└── voicebox{,-mcp,-web}.service
tests/                      # off-device unit tests (mocked audio)
```

## Setup

On the laptop you need `ssh` + `rsync` and a `voicebox` SSH alias (key auth) in
`~/.ssh/config` pointing at `smaniak@172.16.10.117`.

```bash
make deploy            # push code to the Pi
make setup             # bootstrap: apt deps, venv, piper, whisper + wake models
make preflight         # verify every subsystem
make install-services  # systemd; the assistant autostarts on boot
```

`make setup` is idempotent — re-run it any time, and after a reflash.

Config lives in `~/voicebox-app/.env` (created from `config.example.env`). There is
**no API key in it**: the gateway holds the upstream credentials.

## Operating

```bash
make help          # list all shortcuts
make run           # run the assistant in the foreground (see live turns)
make logs          # follow the service logs
make ask Q="..."   # one text turn, no mic — quickest way to test the gateway
make say TEXT="hi" # speak something through the box
make gateway       # check all three model routes from your laptop
make devices       # list audio in/out devices + the resolved output
make audio-diag    # dump the speaker signal chain (amp pin, volume, DAC state)
make stop-all      # free the mic/speaker
```

### Web panel
`make stop-all && make web`, then open `http://172.16.10.117:5000`.
Ask, speak, control music, snapshot the camera, switch backends.

### Via MCP (Claude Code and other agents)
```bash
make mcp-add   # claude mcp add --transport http voicebox http://172.16.10.117:8000/mcp
```
Tools: `speak`, `listen`, `ask(question, backend)`, `set_backend`,
`reset_conversation`, `capture_image`, `take_photo`, `play_music`, `stop_music`,
`skip_track`, `set_volume`, `status`, `list_files/read_file/write_file`,
`restart_service`.

> ⚠️ **One process owns the mic at a time.** The assistant, the MCP server's
> `listen`, and the web panel all contend for it. Leave the assistant as the
> autostart service and `make stop-all` before running another in the foreground.

## Tuning

Measured on the Pi 4: **~2.7 s** from end of speech to spoken reply
(STT 2.0 s + model 0.7 s). Music and volume answer in ~0.05 s — they never
leave the box.

| Symptom | Knob |
|---|---|
| Triggers on the TV | Raise `VOICEBOX_WAKE_THRESHOLD` (0.5 → 0.7) |
| Doesn't hear the wake word | Lower it, or check `make devices` / `VOICEBOX_MIC_DEVICE` |
| Cuts you off mid-sentence | Raise `VOICEBOX_SILENCE_TAIL_S` |
| Waits too long before replying | Lower `VOICEBOX_SILENCE_TAIL_S` |
| Replies are too long-winded | Lower `VOICEBOX_MAX_TOKENS`, tighten `VOICEBOX_SYSTEM_PROMPT` |
| Transcription is poor | Use `ggml-base.en.bin` (2.5x slower here, no better on short commands) |
| STT feels slow | `VOICEBOX_WHISPER_AUDIO_CTX` is the big lever — see `docs/hardware-notes.md` |

## Security notes

The MCP server is **unauthenticated on the LAN by default** (trusted-lab choice)
and exposes file read/write plus service restart, scoped to the app dir. Set
`MCP_AUTH_TOKEN` in `.env` to require `Authorization: Bearer <token>`. Photo names
are sanitized against path traversal and TTS text is never passed through a shell.
The real `.env` is git-ignored and excluded from `deploy.sh`.

## Troubleshooting

### Changing the speaker

Output is one config line — `make devices` lists what's plugged in:

```bash
VOICEBOX_AUDIO_OUT=USB Audio    # match a card NAME (survives reindexing)
VOICEBOX_AUDIO_OUT=plughw:5,0   # or pin an explicit ALSA device
VOICEBOX_ENABLE_HAT_SPEAKER=0   # if you're not using the SunFounder HAT
```

Prefer the name form: card indices move between reboots, names don't. Speech
and music both go through the same `aplay` device, so this is the only knob.

### Common problems

| Symptom | Cause / fix |
|---|---|
| Silent box | See `make audio-diag`. If the DAC shows RUNNING and you still hear nothing, the fault is past the DAC — amp or speaker |
| `piper: not found` | Re-run `make setup`; falls back to espeak meanwhile |
| "couldn't reach the model gateway" | `make gateway` — check AgentGateway on `172.16.10.155` |
| Camera errors | venv must be `--system-site-packages` (system picamera2) |
| Wake word never fires | `make preflight` — openwakeword models may not have downloaded |
| Two things fighting for the mic | `make stop-all` first |

See `docs/hardware-notes.md` for the device log.
