"""Capture-rate handling.

The USB PnP mic on this box rejects 16 kHz outright, so capture runs at the
device's native rate and frames are downsampled before they reach webrtcvad,
openWakeWord and whisper — all of which require exactly 16 kHz.
"""
import unittest
from unittest import mock

import mocks

mocks.install()

try:
    import numpy as np
    HAS_NUMPY = not isinstance(np.int16, str)  # real numpy, not the stub
except ImportError:
    HAS_NUMPY = False

import audio  # noqa: E402
import config  # noqa: E402


@unittest.skipUnless(HAS_NUMPY, "needs real numpy")
class TestResample(unittest.TestCase):
    def test_48k_to_16k_is_an_exact_third(self):
        out = audio.resample(np.zeros(960, dtype=np.int16), 48000)
        self.assertEqual(len(out), 320)

    def test_44100_to_16k_yields_a_full_frame(self):
        out = audio.resample(np.zeros(882, dtype=np.int16), 44100)
        self.assertEqual(len(out), 320)

    def test_matching_rate_is_a_passthrough(self):
        pcm = np.arange(320, dtype=np.int16)
        self.assertIs(audio.resample(pcm, 16000), pcm)

    def test_output_stays_int16(self):
        loud = np.full(960, 32000, dtype=np.int16)
        self.assertEqual(audio.resample(loud, 48000).dtype, np.int16)

    def test_a_tone_keeps_its_shape(self):
        # a 440 Hz tone downsampled from 48k must still be a 440 Hz tone, not
        # aliased noise — this is what naive decimation would get wrong
        t = np.arange(4800) / 48000.0
        tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        out = audio.resample(tone, 48000)
        self.assertEqual(len(out), 1600)
        peak = np.argmax(np.abs(np.fft.rfft(out.astype(np.float32))))
        freq = peak * 16000 / len(out)
        self.assertAlmostEqual(freq, 440, delta=25)


class TestRateSelection(unittest.TestCase):
    def test_prefers_16k_when_the_device_supports_it(self):
        with mock.patch.object(audio.sd, "check_input_settings"):
            self.assertEqual(audio.pick_capture_rate(None), 16000)

    def test_falls_back_to_48k_when_16k_is_rejected(self):
        def check(device=None, samplerate=None, **kw):
            if samplerate == 16000:
                raise RuntimeError("Invalid sample rate")

        with mock.patch.object(audio.sd, "check_input_settings", side_effect=check):
            # 48000 divides to 16000 exactly, so it must win over 44100
            self.assertEqual(audio.pick_capture_rate("USB PnP"), 48000)

    def test_blocksize_matches_the_capture_rate(self):
        with mock.patch.object(audio, "pick_capture_rate", return_value=48000):
            mic = audio.Microphone(device="USB PnP")
        self.assertEqual(mic.rate, 48000)
        self.assertEqual(mic.blocksize, 960)  # 20ms at 48kHz

        with mock.patch.object(audio, "pick_capture_rate", return_value=16000):
            mic = audio.Microphone(device="x")
        self.assertEqual(mic.blocksize, audio.FRAME_SAMPLES)

    def test_explicit_rate_overrides_probing(self):
        probe = mock.Mock()
        with mock.patch.object(audio, "pick_capture_rate", probe):
            mic = audio.Microphone(device="x", rate=44100)
        probe.assert_not_called()
        self.assertEqual(mic.rate, 44100)


@unittest.skipUnless(HAS_NUMPY, "needs real numpy")
class TestFrameSize(unittest.TestCase):
    """webrtcvad rejects any frame that isn't exactly 10/20/30 ms."""

    def _frames(self, rate, blocksize, count=3):
        with mock.patch.object(audio, "pick_capture_rate", return_value=rate):
            mic = audio.Microphone(device="x")
        for _ in range(count):
            mic._q.append(np.zeros(blocksize, dtype=np.int16).tobytes())
        gen = mic.frames()
        return [next(gen) for _ in range(count)]

    def test_downsampled_frames_are_exactly_20ms(self):
        for frame in self._frames(48000, 960):
            self.assertEqual(len(frame), audio.FRAME_SAMPLES * 2)

    def test_odd_rate_frames_are_padded_to_size(self):
        for frame in self._frames(44100, 882):
            self.assertEqual(len(frame), audio.FRAME_SAMPLES * 2)

    def test_native_16k_frames_pass_through_untouched(self):
        for frame in self._frames(16000, audio.FRAME_SAMPLES):
            self.assertEqual(len(frame), audio.FRAME_SAMPLES * 2)


if __name__ == "__main__":
    unittest.main()
