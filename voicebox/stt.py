"""Local speech-to-text via whisper.cpp.

Prefers the pywhispercpp binding so the model stays resident between turns (a
Pi 4 spends ~1s just reloading base.en from disk otherwise); falls back to the
whisper.cpp CLI if the binding isn't installed.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import threading

import numpy as np

import config
import wake

# whisper.cpp emits these for silence/noise; they are not real transcripts.
_NOISE = {"", "[blank_audio]", "(blank_audio)", "[silence]", "(silence)",
          "[music]", "(music)", "you", "thank you.", "thanks for watching!"}


class Transcriber:
    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or config.WHISPER_MODEL
        self._model = None
        self._lock = threading.Lock()
        self._cli = None
        self._load()

    def _load(self) -> None:
        try:
            from pywhispercpp.model import Model
            self._model = Model(self.model_path,
                                n_threads=config.WHISPER_THREADS,
                                audio_ctx=config.WHISPER_AUDIO_CTX,
                                print_progress=False, print_realtime=False)
            return
        except Exception as e:
            print(f"⚠️  pywhispercpp unavailable ({e}); trying whisper.cpp CLI")
        for name in ("whisper-cli", "whisper-cpp", "main"):
            path = shutil.which(name)
            if path:
                self._cli = path
                return
        raise RuntimeError(
            "no local STT available — install pywhispercpp or whisper.cpp "
            "(see scripts/setup_pi.sh)")

    @property
    def backend(self) -> str:
        return "pywhispercpp" if self._model is not None else f"cli:{self._cli}"

    def transcribe_pcm(self, pcm: bytes) -> str:
        """Transcribe raw s16 mono 16 kHz PCM."""
        if not pcm:
            return ""
        if self._model is not None:
            samples = (np.frombuffer(pcm, dtype=np.int16)
                       .astype(np.float32) / 32768.0)
            with self._lock:
                segments = self._model.transcribe(samples)
            text = " ".join(s.text for s in segments)
        else:
            text = self._transcribe_cli(pcm)
        return self._clean(text)

    def _transcribe_cli(self, pcm: bytes) -> str:
        path = None
        try:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            wake.pcm_to_wav(pcm, path)
            with self._lock:
                out = subprocess.run(
                    [self._cli, "-m", self.model_path, "-f", path,
                     "-t", str(config.WHISPER_THREADS), "-nt", "-np"],
                    capture_output=True, text=True, timeout=120)
            return out.stdout
        except subprocess.SubprocessError as e:
            print(f"⚠️  whisper CLI failed: {e}")
            return ""
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    @staticmethod
    def _clean(text: str) -> str:
        text = " ".join(text.split()).strip()
        if text.lower().strip(".!? ") in _NOISE or text.lower() in _NOISE:
            return ""
        return text


_transcriber: Transcriber | None = None
_lock = threading.Lock()


def get_transcriber() -> Transcriber:
    global _transcriber
    with _lock:
        if _transcriber is None:
            _transcriber = Transcriber()
    return _transcriber
