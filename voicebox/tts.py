"""Local text-to-speech via Piper, out through the HAT speaker.

Piper writes raw s16 mono PCM on stdout at the voice's own sample rate (read
from the voice's sidecar .onnx.json). If Piper isn't installed we fall back to
robot_hat's Espeak so the box is never mute.
"""
from __future__ import annotations
import itertools
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


# Spawning the `piper` CLI reloads the 61MB voice model every time: 7.1s just
# to say "Hi.", dwarfing STT (~2s) and the model (~0.7s). Keeping the voice
# resident in-process — the same trick pywhispercpp uses for whisper — drops a
# short reply from 8.0s to 1.2s. Synthesis itself runs at ~0.5x realtime, so
# chunks are streamed straight into one aplay for gapless playback and
# time-to-first-word stops depending on reply length.
_voice = None
_voice_path: str | None = None
_voice_lock = threading.Lock()


def get_voice():
    """Load (once) and return the resident PiperVoice, or None if unavailable."""
    global _voice, _voice_path
    with _voice_lock:
        if _voice is not None and _voice_path == config.PIPER_VOICE:
            return _voice
        if not os.path.exists(config.PIPER_VOICE):
            return None
        try:
            from piper import PiperVoice
        except ImportError:
            return None  # fall back to the CLI path
        try:
            _voice = PiperVoice.load(config.PIPER_VOICE)
            _voice_path = config.PIPER_VOICE
        except Exception as e:
            print(f"⚠️  could not load piper voice: {e}")
            _voice = None
        return _voice


def say(text: str) -> dict:
    """Speak `text` aloud. Serialized so two callers can't overlap."""
    text = (text or "").strip()
    if not text:
        return {"spoke": "", "engine": "none"}
    with _lock:
        voice = get_voice()
        if voice is not None:
            try:
                return _say_streaming(voice, text)
            except Exception as e:
                print(f"⚠️  piper streaming failed ({e}); falling back")
        # resident voice unavailable — try the CLI, then espeak
        if available():
            pcm, rate = synthesize(text)
            if pcm:
                audio.play_pcm(pcm, rate)
                return {"spoke": text, "engine": "piper-cli"}
        return _espeak(text)


def _syn_config():
    """Delivery settings, or None if this piper build predates them."""
    try:
        from piper import SynthesisConfig
    except ImportError:
        return None
    try:
        return SynthesisConfig(length_scale=config.PIPER_LENGTH_SCALE,
                               noise_scale=config.PIPER_NOISE_SCALE,
                               noise_w_scale=config.PIPER_NOISE_W_SCALE)
    except TypeError:
        return None  # older field names; fall back to the voice defaults


def _synthesize_chunks(voice, text: str):
    cfg = _syn_config()
    if cfg is None:
        return voice.synthesize(text)
    return voice.synthesize(text, syn_config=cfg)


def _say_streaming(voice, text: str) -> dict:
    """Pipe synthesis chunks into a single aplay as they are produced."""
    chunks = _synthesize_chunks(voice, text)
    first = next(iter(chunks), None) if not isinstance(chunks, list) else None
    # `synthesize` is a generator; pull the first chunk to learn the rate
    if first is None:
        iterator = iter(_synthesize_chunks(voice, text))
        first = next(iterator, None)
    else:
        iterator = chunks
    if first is None:
        return _espeak(text)

    rate = getattr(first, "sample_rate", 22050)
    channels = getattr(first, "sample_channels", 1)
    audio.enable_speaker()
    proc = subprocess.Popen(audio.play_command(rate, channels),
                            stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    written = 0
    try:
        for chunk in itertools.chain([first], iterator):
            data = audio.compress(chunk.audio_int16_bytes, rate, channels)
            proc.stdin.write(data)
            written += len(data)
    except BrokenPipeError:
        pass  # playback died; nothing useful left to do but stop feeding it
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        proc.wait(timeout=120)
    return {"spoke": text, "engine": "piper", "bytes": written}


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
