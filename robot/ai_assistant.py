"""OpenAI voice+video assistant for PiCrawler.

Loop: record mic -> Whisper STT -> GPT-4o (with a camera frame) -> speak reply
and optionally move the robot via a tool call.

Run on the Pi (needs mic + speaker + clear space):
    ~/picrawler-app/.venv/bin/python ~/picrawler-app/ai_assistant.py
List audio input devices:
    ~/picrawler-app/.venv/bin/python ~/picrawler-app/ai_assistant.py --list-devices

NOTE: this process takes exclusive ownership of the Robot HAT and camera.
Do not run it at the same time as the MCP server or web panel.
"""
import os
import sys
import json
import base64
import tempfile
import subprocess
import wave

sys.path.insert(0, os.path.expanduser("~/picrawler-app"))

# ---- load .env BEFORE importing picrawler_ctl (its battery thresholds are
# read from os.environ at import time) --------------------------------------
_envp = os.path.expanduser("~/picrawler-app/.env")
if os.path.exists(_envp):
    for _line in open(_envp):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402
from openai import OpenAI  # noqa: E402
from picrawler_ctl import get_controller, LowBatteryError  # noqa: E402

SAMPLE_RATE = 16000
RECORD_SECONDS = 5
MIC_DEVICE = os.environ.get("PICRAWLER_MIC_DEVICE")  # index or name; None=default
CHAT_MODEL = os.environ.get("PICRAWLER_CHAT_MODEL", "gpt-4o")

client = OpenAI()  # reads OPENAI_API_KEY
c = get_controller()

MOVE_ACTIONS = {"forward", "backward", "turn_left", "turn_right",
                "stand", "rest", "stop"}
POSE_ACTIONS = {"wave", "push_up", "dance", "look_up", "look_down",
                "look_left", "look_right"}

TOOLS = [{
    "type": "function",
    "function": {
        "name": "control_robot",
        "description": "Move the robot or run an expressive pose.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": sorted(MOVE_ACTIONS | POSE_ACTIONS)},
                "steps": {"type": "integer",
                          "description": "gait cycles for walk/turn (1-5)",
                          "default": 2}},
            "required": ["action"]}}}]

SYSTEM_PROMPT = (
    "You are a friendly quadruped spider robot named PiCrawler. Keep spoken "
    "replies short (one or two sentences). When the user asks you to move, look "
    "somewhere, or perform, call control_robot. You can see through your camera "
    "via the attached image."
)


def list_devices():
    print(sd.query_devices())


def record_wav() -> str:
    print("🎙️  listening (%ds)…" % RECORD_SECONDS)
    kwargs = {}
    if MIC_DEVICE is not None:
        kwargs["device"] = int(MIC_DEVICE) if MIC_DEVICE.isdigit() else MIC_DEVICE
    audio = sd.rec(int(RECORD_SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="int16", **kwargs)
    sd.wait()
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.tobytes())
    return path


def transcribe(path: str) -> str:
    with open(path, "rb") as f:
        return client.audio.transcriptions.create(
            model="whisper-1", file=f).text


def see_b64() -> str:
    return base64.b64encode(c.capture_jpeg_bytes()).decode()


def run_action(action: str, steps=2):
    if action not in MOVE_ACTIONS and action not in POSE_ACTIONS:
        return {"error": f"unknown action {action!r}"}
    try:
        steps = int(steps) if steps is not None else 2
    except (TypeError, ValueError):
        steps = 2
    steps = max(1, min(5, steps))
    try:
        if action in POSE_ACTIONS:
            return c.pose(action)
        fn = getattr(c, action)
        if action in {"forward", "backward", "turn_left", "turn_right"}:
            return fn(steps)
        return fn()
    except LowBatteryError as e:
        return {"error": str(e)}


def think_and_act(text: str, img_b64: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}]}]
    r = client.chat.completions.create(
        model=CHAT_MODEL, messages=messages, tools=TOOLS, max_tokens=250)
    msg = r.choices[0].message
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                continue
            action = args.get("action")
            if action:
                run_action(action, args.get("steps", 2))  # run_action validates
    return msg.content or "Okay."


def say(text: str):
    """Speak via OpenAI TTS through the onboard speaker; fall back to Espeak."""
    out = None
    try:
        fd, out = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        with client.audio.speech.with_streaming_response.create(
                model="tts-1", voice="alloy", input=text) as resp:
            resp.stream_to_file(out)
        # subprocess with an arg list (no shell) — avoids injection; `out` is a
        # tempfile path but we pass it as a single argv element regardless.
        rc = subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", out]).returncode
        if rc != 0:
            raise RuntimeError("ffplay failed")
    except Exception:
        c.speak(text)  # offline Espeak fallback (onboard speaker)
    finally:
        if out and os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass


def main():
    if "--list-devices" in sys.argv:
        list_devices()
        return
    print("🤖 assistant ready — Ctrl-C to quit")
    say("Hi, I am online and ready.")
    while True:
        wav = None
        try:
            wav = record_wav()
            text = transcribe(wav).strip()
            if not text:
                continue
            print("🗣️  you:", text)
            answer = think_and_act(text, see_b64())
            print("🤖 bot:", answer)
            say(answer)
        except KeyboardInterrupt:
            print("\nbye")
            break
        except Exception as e:
            # keep the loop alive on transient mic/camera/OpenAI errors
            print("⚠️  turn failed:", e)
            continue
        finally:
            if wav and os.path.exists(wav):
                try:
                    os.remove(wav)
                except OSError:
                    pass


if __name__ == "__main__":
    main()
