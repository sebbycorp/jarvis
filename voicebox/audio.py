"""Microphone capture and speaker playback.

Capture is a single shared 16 kHz mono int16 stream that both the wake-word
detector and the utterance recorder read from, so the mic is only opened once.

Output goes to whatever `VOICEBOX_AUDIO_OUT` names — "default", an explicit
ALSA device, or a fragment of a card name (preferred; card indices drift across
reboots). Everything, including music, is decoded to raw PCM and piped to one
`aplay` invocation, so there is a single place to point at new hardware.
`enable_speaker()` raises the SunFounder HAT's amp pin and is a harmless no-op
on other hardware.
"""
from __future__ import annotations
import re
import shlex
import shutil
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
        # Frames already handed out, kept so a recording can include the moment
        # *before* the wake word fired — people run "hey jarvis" straight into
        # their request and the first syllables get clipped otherwise.
        self._history: deque[bytes] = deque(
            maxlen=max(1, int(config.PREROLL_S * 1000 / config.FRAME_MS)))
        self._stream: sd.RawInputStream | None = None
        self._event = threading.Event()

    def _callback(self, indata, _frames, _time, status):  # pragma: no cover
        if status:
            pass  # overflows are expected under load; dropping a frame is fine
        # keep the callback cheap — resampling happens in frames()
        self._q.append(bytes(indata))
        self._event.set()

    def _open(self, device):
        kwargs = {"device": device} if device is not None else {}
        stream = sd.RawInputStream(
            samplerate=self.rate, blocksize=self.blocksize,
            channels=1, dtype="int16", callback=self._callback, **kwargs)
        stream.start()
        return stream

    def __enter__(self) -> "Microphone":
        try:
            self._stream = self._open(self.device)
        except Exception as e:
            # A named device disappears from PortAudio's list while another
            # process holds it, so a transient `arecord` used to crash the whole
            # service. Fall back to the default input rather than dying — being
            # deaf for a moment beats not running.
            if self.device is None:
                raise
            print(f"⚠️  mic {self.device!r} unavailable ({e}); using default input")
            self._stream = self._open(None)
            self.device = None
        return self

    def __exit__(self, *exc) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def flush(self) -> None:
        """Drop buffered audio (e.g. our own TTS bleeding into the mic)."""
        self._q.clear()
        self._history.clear()

    def preroll(self) -> bytes:
        """The most recent already-delivered audio, for prepending to a
        recording so the start of the request isn't clipped."""
        return b"".join(self._history)

    def frames(self) -> Iterator[bytes]:
        """Yield 20 ms mono int16 frames at config.SAMPLE_RATE (16 kHz),
        downsampling from the capture rate when the device can't do 16 kHz."""
        while True:
            while self._q:
                raw = self._q.popleft()
                if self.rate == config.SAMPLE_RATE:
                    self._history.append(raw)
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
                frame = out.tobytes()
                self._history.append(frame)
                yield frame
            self._event.clear()
            self._event.wait(timeout=0.5)


def output_device() -> str:
    """Resolve config.AUDIO_OUT to an ALSA device string.

    Accepts "default", an explicit device ("plughw:5,0"), or a fragment of a
    card name ("USB Audio") — the last is preferred, because card indices move
    between reboots but names don't.
    """
    want = config.AUDIO_OUT.strip()
    if not want or want == "default":
        return "default"
    if re.match(r"^(default|sysdefault|plug|hw|dmix|null)[:_]?", want):
        return want
    for index, name in output_cards():
        if want.lower() in name.lower():
            return f"plughw:{index},0"
    return want  # let ALSA report the error rather than guessing


def output_cards() -> list[tuple[int, str]]:
    """(card index, description) for every playback card, from `aplay -l`."""
    if not shutil.which("aplay"):
        return []
    out = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [(int(m.group(1)), m.group(2))
            for m in re.finditer(r"^card (\d+): (.+?),", out.stdout, re.M)]


def play_command(rate: int, channels: int = 1) -> list[str]:
    if config.PLAY_CMD:  # explicit override wins
        return shlex.split(config.PLAY_CMD.format(rate=rate))
    return ["aplay", "-q", "-D", output_device(),
            "-r", str(rate), "-f", "S16_LE", "-c", str(channels), "-"]


# Piper's speech already peaks at 0 dBFS but averages about -18 dBFS — an 18 dB
# crest factor. Perceived loudness tracks RMS, not peaks, which is why it sounds
# quiet on a small speaker even at full volume. Compressing the dynamic range
# lifts RMS ~6 dB (roughly double the loudness) without clipping the peaks.
# Measured on this box: -17.7 -> -12.0 dBFS.
# Deliberately no `gain -n` (normalise): speech is synthesized and compressed
# one sentence-chunk at a time, and normalising each chunk independently makes
# a quiet sentence as loud as a shouted one. A fixed curve plus fixed 1dB of
# headroom is deterministic — the same input always gives the same output — and
# measured louder anyway (-10.8 vs -11.8 dBFS RMS).
COMPAND = ["compand", "0.3,1", "6:-70,-60,-20", "-5", "-90", "0.2",
           "gain", "-1"]


def compress(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Raise perceived loudness via sox. Returns the input unchanged if sox is
    missing or the filter fails — louder is a nicety, audible is the point."""
    if not config.OUTPUT_COMPAND or not pcm or not shutil.which("sox"):
        return pcm
    raw = ["-t", "raw", "-r", str(rate), "-e", "signed", "-b", "16",
           "-c", str(channels)]
    try:
        out = subprocess.run(["sox", *raw, "-", *raw, "-", *COMPAND],
                             input=pcm, capture_output=True, timeout=30)
    except subprocess.SubprocessError:
        return pcm
    return out.stdout if out.returncode == 0 and out.stdout else pcm


def play_pcm(pcm: bytes, rate: int = config.SAMPLE_RATE) -> None:
    """Play raw signed-16 mono PCM through the configured output device."""
    if not pcm:
        return
    enable_speaker()
    subprocess.run(play_command(rate), input=compress(pcm, rate), check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Playback:
    """A decode|play pipeline, stoppable as a unit.

    ffplay picks its own output device and is awkward to point at a specific
    ALSA card, so files are decoded by ffmpeg and piped to the same aplay
    device everything else uses — one output path, one place to configure.
    """

    def __init__(self, path: str, rate: int = 44100, channels: int = 2):
        self._decode = subprocess.Popen(
            ["ffmpeg", "-loglevel", "quiet", "-i", path,
             "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._play = subprocess.Popen(
            play_command(rate, channels), stdin=self._decode.stdout,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # let the decoder see SIGPIPE if playback dies first
        if self._decode.stdout:
            self._decode.stdout.close()

    def poll(self):
        return self._play.poll()

    def wait(self, timeout: float | None = None):
        return self._play.wait(timeout=timeout)

    def terminate(self) -> None:
        for p in (self._play, self._decode):
            if p.poll() is None:
                p.terminate()

    def kill(self) -> None:
        for p in (self._play, self._decode):
            if p.poll() is None:
                p.kill()


def play_file(path: str) -> Playback:
    """Play an audio file (mp3/wav/flac). Returns a handle so it can be
    stopped — used by the music player."""
    enable_speaker()
    return Playback(path)


_earcon_cache: bytes | None = None


def earcon() -> bytes:
    """A short sine blip with a raised-cosine envelope.

    Generated rather than shipped as a file, and the envelope matters: a raw
    sine that starts and stops at full amplitude clicks audibly on a small
    speaker.
    """
    global _earcon_cache
    if _earcon_cache is not None:
        return _earcon_cache
    n = int(config.SAMPLE_RATE * config.EARCON_MS / 1000)
    t = np.arange(n) / config.SAMPLE_RATE
    wave = np.sin(2 * np.pi * config.EARCON_HZ * t)
    fade = max(1, n // 8)
    envelope = np.ones(n)
    ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, fade)))
    envelope[:fade] = ramp
    envelope[-fade:] = ramp[::-1]
    _earcon_cache = (wave * envelope * 8000).astype(np.int16).tobytes()
    return _earcon_cache


def play_earcon() -> None:
    """Signal 'I'm listening'. Never let a failed blip break a turn."""
    if not config.EARCON:
        return
    try:
        enable_speaker()
        # bypass compress(): the tone is already at a chosen level, and running
        # it through compand would just add latency to the one thing that must
        # be instant
        subprocess.run(play_command(config.SAMPLE_RATE), input=earcon(),
                       check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass


def list_devices() -> str:
    lines = ["--- inputs (sounddevice) ---", str(sd.query_devices()),
             "", "--- outputs (aplay -l) ---"]
    for index, name in output_cards():
        lines.append(f"  card {index}: {name}")
    lines.append(f"\nVOICEBOX_AUDIO_OUT={config.AUDIO_OUT!r} "
                 f"-> {output_device()}")
    return "\n".join(lines)
