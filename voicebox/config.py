"""Central config for the voice box.

Everything is read from the environment, with `.env` in the app dir applied
first. Import this module before anything that reads os.environ at import time.
"""
from __future__ import annotations
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("VOICEBOX_APP_DIR",
                              os.path.expanduser("~/voicebox-app"))).resolve()


def load_env(path: Path | None = None) -> None:
    """Apply KEY=VALUE lines from .env into os.environ (does not overwrite)."""
    env = path or (APP_DIR / ".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def _s(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()


def _i(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def _b(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


# ---- model gateway (AgentGateway) -----------------------------------------
GATEWAY_HOST = _s("VOICEBOX_GATEWAY_HOST", "172.16.10.155")
DEFAULT_BACKEND = _s("VOICEBOX_DEFAULT_BACKEND", "local")
LLM_TIMEOUT = _f("VOICEBOX_LLM_TIMEOUT", 60.0)
MAX_TOKENS = _i("VOICEBOX_MAX_TOKENS", 400)
HISTORY_TURNS = _i("VOICEBOX_HISTORY_TURNS", 6)

# Qwen is a reasoning model: left alone it spends the whole token budget
# thinking and returns empty `content`. A voice assistant wants the answer, not
# the deliberation, so thinking is off by default.
LOCAL_THINKING = _b("VOICEBOX_LOCAL_THINKING", False)

# name -> (url template, model id). An empty model means "let the gateway pick".
BACKENDS: dict[str, dict] = {
    "local": {
        "url": _s("VOICEBOX_LOCAL_URL",
                  "http://{host}:31944/spark/v1/chat/completions"),
        "model": _s("VOICEBOX_LOCAL_MODEL", "Qwen/Qwen3.6-35B-A3B-FP8"),
        "vision": _b("VOICEBOX_LOCAL_VISION", False),
        "label": "local Qwen",
        "extra": ({} if LOCAL_THINKING else
                  {"chat_template_kwargs": {"enable_thinking": False}}),
    },
    "openai": {
        "url": _s("VOICEBOX_OPENAI_URL",
                  "http://{host}:30160/openai/v1/chat/completions"),
        "model": _s("VOICEBOX_OPENAI_MODEL", ""),
        "vision": _b("VOICEBOX_OPENAI_VISION", True),
        "label": "GPT",
    },
    "grok": {
        "url": _s("VOICEBOX_GROK_URL",
                  "http://{host}:31397/grok/v1/chat/completions"),
        "model": _s("VOICEBOX_GROK_MODEL", "grok-4.5"),
        "vision": _b("VOICEBOX_GROK_VISION", True),
        "label": "Grok",
    },
}

# ---- audio -----------------------------------------------------------------
SAMPLE_RATE = 16000
# 20ms @ 16kHz = 320 samples: a valid webrtcvad frame, and 4 of them are the
# 1280-sample chunk openWakeWord expects.
FRAME_MS = 20
MIC_DEVICE = os.environ.get("VOICEBOX_MIC_DEVICE") or None
# 0 = probe the device for a rate it supports. The USB PnP mic on this box
# rejects 16 kHz, so capture runs at 48 kHz and audio.py downsamples.
MIC_RATE = _i("VOICEBOX_MIC_RATE", 0)
# ALSA output device. "default" follows /etc/asound.conf; set a name fragment
# (e.g. "USB Audio") or an explicit device ("plughw:5,0") to pin a specific
# card — card indices drift across reboots, names don't.
AUDIO_OUT = _s("VOICEBOX_AUDIO_OUT", "default")
# Compress speech dynamics before playback (needs sox). Piper averages ~-18
# dBFS despite peaking at 0; this lifts it ~6 dB so it carries on a small
# speaker. Turn off if you have a good amp and prefer untouched dynamics.
OUTPUT_COMPAND = _b("VOICEBOX_OUTPUT_COMPAND", True)
# Short tone played the instant the wake word fires. Without it you talk into
# silence with no idea whether the box heard you.
EARCON = _b("VOICEBOX_EARCON", True)
EARCON_HZ = _i("VOICEBOX_EARCON_HZ", 880)
EARCON_MS = _i("VOICEBOX_EARCON_MS", 120)
PLAY_CMD = _s("VOICEBOX_PLAY_CMD", "")  # empty = build it from AUDIO_OUT
# The SunFounder HAT needs GPIO20 raised to power its amplifier. Harmless to
# leave on with other hardware, but turn it off if you drop the HAT entirely.
ENABLE_HAT_SPEAKER = _b("VOICEBOX_ENABLE_HAT_SPEAKER", True)

# ---- wake word -------------------------------------------------------------
WAKE_ENABLED = _b("VOICEBOX_WAKE_ENABLED", True)
WAKE_MODEL = _s("VOICEBOX_WAKE_MODEL", "hey_jarvis")
WAKE_THRESHOLD = _f("VOICEBOX_WAKE_THRESHOLD", 0.5)

# ---- speech capture --------------------------------------------------------
VAD_AGGRESSIVENESS = _i("VOICEBOX_VAD_AGGRESSIVENESS", 2)
MAX_UTTERANCE_S = _f("VOICEBOX_MAX_UTTERANCE_S", 15.0)
SILENCE_TAIL_S = _f("VOICEBOX_SILENCE_TAIL_S", 0.8)
MIN_UTTERANCE_S = _f("VOICEBOX_MIN_UTTERANCE_S", 0.4)
# Audio retained from *before* the wake word fires and prepended to the
# recording. People run "hey jarvis" straight into their request, so the first
# syllables land during detection and were being lost ("ask Grok" -> "rock").
PREROLL_S = _f("VOICEBOX_PREROLL_S", 0.6)

# ---- STT / TTS (both local) ------------------------------------------------
# tiny.en transcribes these short commands identically to base.en on this box
# but 2.5x faster (2.2x realtime vs 5.5x on a Pi 4) — latency matters more than
# marginal accuracy for a voice assistant.
WHISPER_MODEL = _s("VOICEBOX_WHISPER_MODEL", str(APP_DIR / "models/ggml-tiny.en.bin"))
WHISPER_THREADS = _i("VOICEBOX_WHISPER_THREADS", 4)
# Whisper pads every clip to a 30s window (audio_ctx 1500). Our utterances are
# capped at MAX_UTTERANCE_S, so shrinking the encoder window halves inference
# time with no transcript change. 768/1500 * 30s = 15.4s of coverage.
WHISPER_AUDIO_CTX = _i("VOICEBOX_WHISPER_AUDIO_CTX", 768)
PIPER_BIN = _s("VOICEBOX_PIPER_BIN", "piper")
PIPER_VOICE = _s("VOICEBOX_PIPER_VOICE", str(APP_DIR / "models/en_US-amy-medium.onnx"))

# ---- camera ----------------------------------------------------------------
CAMERA_ENABLED = _b("VOICEBOX_CAMERA_ENABLED", True)
PHOTO_DIR = _s("VOICEBOX_PHOTO_DIR", str(APP_DIR / "photos"))

# ---- music -----------------------------------------------------------------
MUSIC_DIR = _s("VOICEBOX_MUSIC_DIR", str(APP_DIR / "music"))

# ---- servers ---------------------------------------------------------------
MCP_HOST = _s("MCP_HOST", "0.0.0.0")
MCP_PORT = _i("MCP_PORT", 8000)
MCP_PATH = _s("MCP_PATH", "/mcp")
MCP_AUTH_TOKEN = _s("MCP_AUTH_TOKEN", "")
WEB_HOST = _s("WEB_HOST", "0.0.0.0")
WEB_PORT = _i("WEB_PORT", 5000)

WAKE_NAME = _s("VOICEBOX_NAME", "Jarvis")
SYSTEM_PROMPT = _s(
    "VOICEBOX_SYSTEM_PROMPT",
    f"You are {WAKE_NAME}, a helpful voice assistant living in a small speaker. "
    "Your replies are spoken aloud, so keep them short and conversational — one "
    "or two sentences unless asked for detail. Never use markdown, bullet points, "
    "emoji, or code blocks. Spell out numbers and units the way a person says them.",
)


def backend_url(name: str) -> str:
    return BACKENDS[name]["url"].format(host=GATEWAY_HOST)
