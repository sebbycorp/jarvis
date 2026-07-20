"""The always-on voice loop.

    wake word -> record until silence -> local STT -> intent -> speak

Local intents (music, volume, stop) are handled without a round trip to a
model. Everything else goes to the gateway router, with a camera frame attached
when the question sounds visual.

Run on the Pi:
    ~/voicebox-app/.venv/bin/python ~/voicebox-app/assistant.py
    ... --list-devices      show input devices and exit
    ... --once "question"   one text turn, no mic (for testing)
"""
from __future__ import annotations
import re
import sys
import time

import audio
import config
import llm
import music
import stt
import tts
import wake

VISION_RE = re.compile(
    r"\b(what|who|how many|describe|read)\b.{0,40}\b(see|seeing|look|looking|"
    r"front of you|camera|this|here|holding|picture|photo|room)\b|"
    r"\b(look at|take a look|check out) (this|that|it)\b", re.I)

MUSIC_PLAY_RE = re.compile(r"^\s*(?:please\s+)?play\s+(?:some\s+|the\s+)?(.*)$", re.I)
MUSIC_STOP_RE = re.compile(r"^\s*(?:stop|pause|silence)\b.*\b(music|song|playing|it)\b|"
                           r"^\s*(?:stop|pause)\s*$", re.I)
MUSIC_SKIP_RE = re.compile(r"\b(next|skip)\b.*\b(track|song)\b|^\s*(next|skip)\s*$", re.I)
VOLUME_RE = re.compile(r"\b(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\b|"
                       r"\b(turn it |turn the volume )?(up|down)\b.*\bvolume\b|"
                       r"\bvolume\s+(up|down)\b", re.I)
RESET_RE = re.compile(r"^\s*(?:forget|reset|clear)\b.*\b(context|history|conversation|that)\b",
                      re.I)


class Assistant:
    def __init__(self):
        self.router = llm.get_router()
        self.player = music.get_player()
        self.transcriber = stt.get_transcriber()
        self.wake = wake.WakeWord()
        self.recorder = wake.Recorder()
        self._volume = 70

    # ---- one turn ----------------------------------------------------------
    def handle(self, text: str) -> str:
        """Route one utterance and return what should be spoken."""
        text = text.strip()
        if not text:
            return ""

        if RESET_RE.match(text):
            self.router.reset()
            return "Okay, I've forgotten our conversation."

        if MUSIC_STOP_RE.search(text) and self.player.is_playing:
            self.player.stop()
            return "Stopped."

        if MUSIC_SKIP_RE.search(text) and self.player.is_playing:
            r = self.player.skip()
            return f"Playing {r['playing']}." if r["playing"] else "That was the last track."

        m = VOLUME_RE.search(text)
        if m:
            if m.group(1):
                self._volume = int(m.group(1))
            else:
                direction = m.group(3) or m.group(4) or ""
                self._volume += 15 if direction.lower() == "up" else -15
            self._volume = max(0, min(100, self._volume))
            r = self.player.set_volume(self._volume)
            if "error" in r:
                return "I couldn't change the volume."
            return f"Volume {self._volume} percent."

        m = MUSIC_PLAY_RE.match(text)
        if m and not VISION_RE.search(text):
            query = m.group(1).strip().rstrip(".?!")
            shuffle = bool(re.search(r"\b(shuffle|random|anything|music)\b", query, re.I))
            if re.fullmatch(r"(some\s+)?music|anything|something", query, re.I):
                query = ""
            r = self.player.play(query or None, shuffle=shuffle)
            if r.get("error"):
                return f"I couldn't find {query}." if query else "I have no music loaded."
            return f"Playing {r['playing']}."

        image = None
        if VISION_RE.search(text):
            try:
                image = camera_frame()
            except Exception as e:
                print(f"⚠️  camera: {e}")

        try:
            result = self.router.ask(text, image_jpeg=image)
        except llm.LLMError as e:
            print(f"⚠️  {e}")
            return "Sorry, I couldn't reach the model gateway."
        if not result["switched"]:
            print(f"   ↳ via {self.router.label(result['backend'])}"
                  f"{' + vision' if result['saw_image'] else ''}")
        return result["reply"]

    # ---- the loop ----------------------------------------------------------
    def run(self) -> None:
        listening = "say the wake word" if self.wake.available else "just talk"
        print(f"🔊 {config.WAKE_NAME} ready — {listening}. Ctrl-C to quit.")
        print(f"   stt={self.transcriber.backend}  "
              f"tts={'piper' if tts.available() else 'espeak'}  "
              f"default={self.router.label()}")
        tts.say(f"{config.WAKE_NAME} online.")

        with audio.Microphone() as mic:
            frames = mic.frames()
            while True:
                try:
                    mic.flush()
                    if not self.wake.wait(frames):
                        break
                    print(f"👂 listening… (wake {self.wake.last_score:.2f})")
                    # Grab the pre-roll first: it holds the moment before the
                    # trigger, so a request run straight into the wake word
                    # doesn't lose its start. Then beep, then drop whatever the
                    # mic picked up meanwhile — otherwise our own blip lands at
                    # the head of the recording and the VAD reads it as speech.
                    preroll = mic.preroll()
                    audio.play_earcon()
                    mic.flush()
                    pcm = preroll + self.recorder.record(frames)
                    if not pcm:
                        continue

                    t0 = time.monotonic()
                    text = self.transcriber.transcribe_pcm(pcm)
                    if not text:
                        continue
                    print(f"🗣️  {text}   ({time.monotonic() - t0:.1f}s)")

                    reply = self.handle(text)
                    if reply:
                        print(f"🤖 {reply}")
                        tts.say(reply)
                except KeyboardInterrupt:
                    print("\nbye")
                    tts.say("Goodbye.")
                    return
                except Exception as e:
                    # a bad mic read or a gateway hiccup must not kill the box
                    print(f"⚠️  turn failed: {e}")
                    continue


def camera_frame() -> bytes:
    import camera
    return camera.get_camera().capture_jpeg()


def main() -> None:
    if "--list-devices" in sys.argv:
        print(audio.list_devices())
        return
    if "--once" in sys.argv:
        i = sys.argv.index("--once")
        question = " ".join(sys.argv[i + 1:]).strip()
        if not question:
            print("usage: assistant.py --once \"your question\"")
            sys.exit(2)
        a = Assistant.__new__(Assistant)  # skip mic/STT init for a text turn
        a.router, a.player, a._volume = llm.get_router(), music.get_player(), 70
        print(a.handle(question))
        return
    Assistant().run()


if __name__ == "__main__":
    main()
