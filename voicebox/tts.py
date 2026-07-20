"""Local text-to-speech via Piper, out through the HAT speaker.

Piper writes raw s16 mono PCM on stdout at the voice's own sample rate (read
from the voice's sidecar .onnx.json). If Piper isn't installed we fall back to
robot_hat's Espeak so the box is never mute.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import threading

import audio
import config

_lock = threading.Lock()
_warned = False


def _voice_rate(voice: str) -> int:
    """Piper voices are usually 22050 Hz, but medium/high vary — read the
    sidecar config rather than guessing."""
    for candidate in (voice + ".json", os.path.splitext(voice)[0] + ".onnx.json"):
        if os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    return int(json.load(f)["audio"]["sample_rate"])
            except (ValueError, KeyError, OSError):
                break
    return 22050


def available() -> bool:
    return bool(shutil.which(config.PIPER_BIN)) and os.path.exists(config.PIPER_VOICE)


def synthesize(text: str) -> tuple[bytes, int]:
    """Return (raw s16 mono PCM, sample_rate). Empty PCM if Piper is missing."""
    text = (text or "").strip()
    if not text or not available():
        return b"", config.SAMPLE_RATE
    rate = _voice_rate(config.PIPER_VOICE)
    try:
        out = subprocess.run(
            [config.PIPER_BIN, "--model", config.PIPER_VOICE, "--output_raw"],
            input=text.encode(), capture_output=True, timeout=120)
    except subprocess.SubprocessError as e:
        print(f"⚠️  piper failed: {e}")
        return b"", rate
    if out.returncode != 0:
        print(f"⚠️  piper exited {out.returncode}: {out.stderr[:200].decode(errors='replace')}")
        return b"", rate
    return out.stdout, rate


def say(text: str) -> dict:
    """Speak `text` aloud. Serialized so two callers can't overlap."""
    text = (text or "").strip()
    if not text:
        return {"spoke": "", "engine": "none"}
    with _lock:
        pcm, rate = synthesize(text)
        if pcm:
            audio.play_pcm(pcm, rate)
            return {"spoke": text, "engine": "piper"}
        return _espeak(text)


def _espeak(text: str) -> dict:
    global _warned
    if not _warned:
        print("⚠️  piper unavailable — falling back to espeak "
              "(run scripts/setup_pi.sh to install voices)")
        _warned = True
    audio.enable_speaker()
    try:
        from robot_hat.tts import Espeak
        Espeak().say(text)
        return {"spoke": text, "engine": "espeak-hat"}
    except Exception:
        pass
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        # arg list, no shell: `text` is untrusted (MCP `speak` is unauthenticated
        # on the LAN) but can never be parsed as a command here.
        subprocess.run([binary, text], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"spoke": text, "engine": "espeak"}
    return {"spoke": "", "engine": "none", "error": "no TTS engine available"}
