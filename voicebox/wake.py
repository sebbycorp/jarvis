"""Wake-word detection (openWakeWord) and VAD-bounded utterance capture.

Both consume the shared 16 kHz frame stream from audio.Microphone.
"""
from __future__ import annotations
import time
from collections.abc import Iterator

import numpy as np

import audio
import config


class WakeWord:
    """Wraps openWakeWord. Falls back to 'always awake' if it isn't installed,
    so the box still works (push-to-talk style) on a bare install."""

    CHUNK = 1280  # openWakeWord's expected 80ms @ 16kHz input

    def __init__(self, model: str | None = None,
                 threshold: float | None = None):
        self.threshold = threshold if threshold is not None else config.WAKE_THRESHOLD
        self.model_name = model or config.WAKE_MODEL
        self._buf = np.empty(0, dtype=np.int16)
        self._model = None
        self.available = False
        self.last_score = 0.0
        if not config.WAKE_ENABLED:
            return
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[self.model_name],
                                inference_framework="onnx")
            self.available = True
        except Exception as e:  # missing package or model file
            print(f"⚠️  wake word unavailable ({e}); listening continuously")

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.int16)
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass

    def detect(self, frame: bytes) -> float:
        """Feed one mic frame. Returns the best score for this chunk (0 if the
        chunk isn't complete yet)."""
        if self._model is None:
            return 1.0  # no model: every frame "triggers"
        self._buf = np.concatenate(
            [self._buf, np.frombuffer(frame, dtype=np.int16)])
        best = 0.0
        while len(self._buf) >= self.CHUNK:
            chunk, self._buf = self._buf[:self.CHUNK], self._buf[self.CHUNK:]
            scores = self._model.predict(chunk)
            best = max(best, max(scores.values()) if scores else 0.0)
        return best

    def wait(self, frames: Iterator[bytes]) -> bool:
        """Block until the wake word fires. Returns False if the stream ends.

        `last_score` holds the triggering score — log it when tuning: a genuine
        wake word usually scores well above 0.9, while background speech that
        sneaks past sits just over the threshold.
        """
        self.last_score = 0.0
        if self._model is None:
            next(frames, None)
            return True
        self.reset()
        for frame in frames:
            score = self.detect(frame)
            if score >= self.threshold:
                self.last_score = score
                self.reset()
                return True
        return False


class Recorder:
    """Captures one utterance, ending on trailing silence."""

    def __init__(self, aggressiveness: int | None = None):
        level = (aggressiveness if aggressiveness is not None
                 else config.VAD_AGGRESSIVENESS)
        self._vad = None
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(level)
        except Exception as e:
            print(f"⚠️  webrtcvad unavailable ({e}); using energy-based VAD")

    def _is_speech(self, frame: bytes) -> bool:
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame, config.SAMPLE_RATE)
            except Exception:
                pass
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return False
        return float(np.sqrt(np.mean(samples ** 2))) > 500.0

    def record(self, frames: Iterator[bytes]) -> bytes:
        """Collect PCM until the speaker stops. Returns raw s16 mono PCM.

        Leading silence is tolerated up to MAX_UTTERANCE_S so a slow start
        doesn't cut the user off; once speech begins, SILENCE_TAIL_S of quiet
        ends the turn.
        """
        collected: list[bytes] = []
        started = False
        silence = 0.0
        start = time.monotonic()
        frame_s = config.FRAME_MS / 1000.0

        for frame in frames:
            speech = self._is_speech(frame)
            if speech:
                started = True
                silence = 0.0
                collected.append(frame)
            elif started:
                silence += frame_s
                collected.append(frame)  # keep the tail; whisper likes padding
                if silence >= config.SILENCE_TAIL_S:
                    break
            if time.monotonic() - start > config.MAX_UTTERANCE_S:
                break

        pcm = b"".join(collected)
        if len(pcm) < config.MIN_UTTERANCE_S * config.SAMPLE_RATE * 2:
            return b""
        return pcm


def pcm_to_wav(pcm: bytes, path: str,
               rate: int = config.SAMPLE_RATE) -> str:
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path
