"""Health check — run before operating the box.

    ~/voicebox-app/.venv/bin/python ~/voicebox-app/preflight.py

Checks each subsystem independently and reports what is degraded rather than
dying on the first failure. Exit code 1 if anything essential is broken.
"""
from __future__ import annotations
import os
import shutil
import sys

import config

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
_essential_failed = False


def report(level: str, name: str, detail: str = "", essential: bool = False) -> None:
    global _essential_failed
    if level is FAIL and essential:
        _essential_failed = True
    print(f"{level} {name:<22} {detail}")


def check_gateway() -> None:
    import requests
    for name in sorted(config.BACKENDS):
        url = config.backend_url(name)
        payload = {"model": config.BACKENDS[name]["model"],
                   "messages": [{"role": "user", "content": "ping"}],
                   "max_tokens": 5}
        try:
            r = requests.post(url, json=payload, timeout=20)
        except Exception as e:
            report(WARN, f"gateway:{name}", f"unreachable — {e}")
            continue
        if r.status_code == 200:
            report(OK, f"gateway:{name}", url.split("//")[1].split("/")[0])
        else:
            report(WARN, f"gateway:{name}", f"HTTP {r.status_code}: {r.text[:80]}")


def check_mic() -> None:
    try:
        import sounddevice as sd
        ins = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception as e:
        report(FAIL, "microphone", str(e), essential=True)
        return
    if not ins:
        report(FAIL, "microphone", "no input devices", essential=True)
        return
    report(OK, "microphone", f"{len(ins)} input(s): {ins[0]['name']}")


def check_speaker() -> None:
    import audio
    enabled = audio.enable_speaker()
    play = config.PLAY_CMD.split()[0]
    if not shutil.which(play):
        report(FAIL, "speaker", f"{play} not installed", essential=True)
        return
    report(OK, "speaker", f"{play}"
           + (" (HAT amp enabled)" if enabled else " (HAT amp not available)"))


def check_stt() -> None:
    if not os.path.exists(config.WHISPER_MODEL):
        report(FAIL, "stt model", f"missing {config.WHISPER_MODEL}", essential=True)
        return
    try:
        import stt
        report(OK, "stt", f"{stt.get_transcriber().backend} "
                          f"({os.path.basename(config.WHISPER_MODEL)})")
    except Exception as e:
        report(FAIL, "stt", str(e), essential=True)


def check_tts() -> None:
    import tts
    if tts.available():
        report(OK, "tts", f"piper ({os.path.basename(config.PIPER_VOICE)})")
    elif shutil.which("espeak-ng") or shutil.which("espeak"):
        report(WARN, "tts", "piper missing — falling back to espeak")
    else:
        report(FAIL, "tts", "no TTS engine", essential=True)


def check_wake() -> None:
    if not config.WAKE_ENABLED:
        report(WARN, "wake word", "disabled — always listening")
        return
    try:
        import wake
        w = wake.WakeWord()
        if w.available:
            report(OK, "wake word", f"{config.WAKE_MODEL} @ {config.WAKE_THRESHOLD}")
        else:
            report(WARN, "wake word", "openwakeword unavailable — always listening")
    except Exception as e:
        report(WARN, "wake word", str(e))


def check_vad() -> None:
    try:
        import webrtcvad  # noqa: F401
        report(OK, "vad", f"webrtcvad level {config.VAD_AGGRESSIVENESS}")
    except Exception:
        report(WARN, "vad", "webrtcvad missing — using energy threshold")


def check_camera() -> None:
    if not config.CAMERA_ENABLED:
        report(WARN, "camera", "disabled (VOICEBOX_CAMERA_ENABLED=0)")
        return
    try:
        import camera
        n = len(camera.get_camera().capture_jpeg())
        report(OK, "camera", f"captured {n // 1024} KB frame")
    except camera.CameraError as e:
        # not essential — only vision questions need it
        report(WARN, "camera", str(e))
    except Exception as e:
        report(WARN, "camera", f"{e}")


def check_music() -> None:
    import music
    n = len(music.get_player().library())
    if not shutil.which("ffplay"):
        report(WARN, "music", "ffplay not installed")
        return
    report(OK if n else WARN, "music",
           f"{n} track(s) in {config.MUSIC_DIR}")


def main() -> None:
    print(f"— {config.WAKE_NAME} preflight —  app dir: {config.APP_DIR}\n")
    for check in (check_mic, check_speaker, check_stt, check_tts, check_wake,
                  check_vad, check_camera, check_music, check_gateway):
        try:
            check()
        except Exception as e:  # a broken check must not hide the others
            report(WARN, check.__name__.replace("check_", ""), f"check errored: {e}")
    print()
    if _essential_failed:
        print("❌ essential checks failed — see scripts/setup_pi.sh")
        sys.exit(1)
    print("✅ ready")


if __name__ == "__main__":
    main()
