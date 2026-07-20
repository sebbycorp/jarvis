"""Microphone capture and speaker playback.

Output goes to the Robot HAT's hifiberry DAC (the board is still installed even
though the legs are gone); `enable_speaker` toggles the HAT's amplifier pin.
Capture is a single shared 16 kHz mono int16 stream that both the wake-word
detector and the utterance recorder read from, so the mic is only opened once.
"""
from __future__ import annotations
import shlex
import subprocess
import threading
from collections import deque
from collections.abc import Iterator
from fractions import Fraction

import numpy as np
import sounddevice as sd

import config

FRAME_SAMPLES = config.SAMPLE_RATE * config.FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2

# whisper, openWakeWord and webrtcvad all require 16 kHz, but this box's USB PnP
# mic only offers 44100/48000 — opening it at 16000 fails with "Invalid sample
# rate". We capture at a rate the device actually supports and downsample in
# the consumer, rather than routing through ALSA/Pulse "default" to resample.
CANDIDATE_RATES = (16000, 48000, 32000, 44100)


def pick_capture_rate(device) -> int:
    """First supported rate, preferring ones that divide down to 16 kHz exactly."""
    for rate in CANDIDATE_RATES:
        try:
            sd.check_input_settings(device=device, samplerate=rate,
                                    channels=1, dtype="int16")
            return rate
        except Exception:
            continue
    # nothing probed cleanly; let PortAudio pick and resample from there
    try:
        return int(sd.query_devices(device, "input")["default_samplerate"])
    except Exception:
        return config.SAMPLE_RATE


def resample(pcm: np.ndarray, src: int, dst: int = config.SAMPLE_RATE) -> np.ndarray:
    """Rate-convert mono int16. Polyphase when scipy is available (it ships as
    an openwakeword dependency); plain decimation for integer ratios otherwise."""
    if src == dst:
        return pcm
    ratio = Fraction(dst, src).limit_denominator(1000)
    try:
        from scipy.signal import resample_poly
        out = resample_poly(pcm.astype(np.float32),
                            ratio.numerator, ratio.denominator)
    except ImportError:
        if src % dst:
            raise RuntimeError(
                f"cannot resample {src}->{dst} without scipy") from None
        out = pcm.astype(np.float32)[::src // dst]
    return np.clip(out, -32768, 32767).astype(np.int16)

_speaker_ready = False
_speaker_lock = threading.Lock()


def enable_speaker() -> bool:
    """Power the HAT amplifier. Safe to call repeatedly; no-op off-Pi."""
    global _speaker_ready
    with _speaker_lock:
        if _speaker_ready:
            return True
        if not config.ENABLE_HAT_SPEAKER:
            _speaker_ready = True
            return False
        try:
            from robot_hat.utils import enable_speaker as _en
            _en()
        except Exception:
            try:
                from robot_hat.tts import enable_speaker as _en  # older layout
                _en()
            except Exception:
                _speaker_ready = True
                return False
        _speaker_ready = True
        return True


class Microphone:
    """Shared mic stream yielding fixed-size PCM frames.

    Use as a context manager. `frames()` may be called from one consumer at a
    time; the assistant loop hands the stream between wake detection and
    utterance capture rather than opening two streams.
    """

    def __init__(self, device: str | int | None = None,
                 rate: int | None = None):
        if device is None:
            device = config.MIC_DEVICE
        if isinstance(device, str) and device.isdigit():
            device = int(device)
        self.device = device
        self.rate = rate or config.MIC_RATE or pick_capture_rate(device)
        # blocksize in *capture* samples that yields one 16 kHz output frame
        self.blocksize = int(round(self.rate * config.FRAME_MS / 1000))
        self._q: deque[bytes] = deque(maxlen=200)  # ~4s of backlog
        self._stream: sd.RawInputStream | None = None
        self._event = threading.Event()

    def _callback(self, indata, _frames, _time, status):  # pragma: no cover
        if status:
            pass  # overflows are expected under load; dropping a frame is fine
        # keep the callback cheap — resampling happens in frames()
        self._q.append(bytes(indata))
        self._event.set()

    def __enter__(self) -> "Microphone":
        kwargs = {"device": self.device} if self.device is not None else {}
        self._stream = sd.RawInputStream(
            samplerate=self.rate, blocksize=self.blocksize,
            channels=1, dtype="int16", callback=self._callback, **kwargs)
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def flush(self) -> None:
        """Drop buffered audio (e.g. our own TTS bleeding into the mic)."""
        self._q.clear()

    def frames(self) -> Iterator[bytes]:
        """Yield 20 ms mono int16 frames at config.SAMPLE_RATE (16 kHz),
        downsampling from the capture rate when the device can't do 16 kHz."""
        while True:
            while self._q:
                raw = self._q.popleft()
                if self.rate == config.SAMPLE_RATE:
                    yield raw
                    continue
                block = np.frombuffer(raw, dtype=np.int16)
                out = resample(block, self.rate)
                # keep frames exactly FRAME_SAMPLES long: webrtcvad rejects
                # anything else, and openWakeWord's chunking assumes it
                if len(out) > FRAME_SAMPLES:
                    out = out[:FRAME_SAMPLES]
                elif len(out) < FRAME_SAMPLES:
                    out = np.pad(out, (0, FRAME_SAMPLES - len(out)))
                yield out.tobytes()
            self._event.clear()
            self._event.wait(timeout=0.5)


def play_pcm(pcm: bytes, rate: int = config.SAMPLE_RATE) -> None:
    """Play raw signed-16 mono PCM through the configured output command."""
    if not pcm:
        return
    enable_speaker()
    cmd = shlex.split(config.PLAY_CMD.format(rate=rate))
    subprocess.run(cmd, input=pcm, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_file(path: str) -> subprocess.Popen:
    """Play an audio file (mp3/wav/flac). Returns the process so it can be
    stopped — used by the music player."""
    enable_speaker()
    return subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def list_devices() -> str:
    return str(sd.query_devices())
