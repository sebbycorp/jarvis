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


APLAY_L = """**** List of PLAYBACK Hardware Devices ****
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 []
card 2: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones []
card 3: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: HifiBerry DAC HiFi []
card 5: Device [USB Audio Device], device 0: USB Audio []
"""


class TestOutputDevice(unittest.TestCase):
    def setUp(self):
        mock.patch.object(audio.shutil, "which",
                          return_value="/usr/bin/aplay").start()
        mock.patch.object(audio.subprocess, "run",
                          return_value=mock.Mock(returncode=0, stdout=APLAY_L)).start()
        self.addCleanup(mock.patch.stopall)

    def _resolve(self, value):
        with mock.patch.object(audio.config, "AUDIO_OUT", value):
            return audio.output_device()

    def test_default_passes_through(self):
        self.assertEqual(self._resolve("default"), "default")
        self.assertEqual(self._resolve(""), "default")

    def test_explicit_alsa_device_is_used_verbatim(self):
        self.assertEqual(self._resolve("plughw:5,0"), "plughw:5,0")
        self.assertEqual(self._resolve("hw:3,0"), "hw:3,0")

    def test_a_card_name_resolves_to_its_index(self):
        # the point of naming: a USB speaker keeps working when its index moves
        self.assertEqual(self._resolve("USB Audio"), "plughw:5,0")
        self.assertEqual(self._resolve("hifiberry"), "plughw:3,0")

    def test_name_match_is_case_insensitive(self):
        self.assertEqual(self._resolve("usb audio"), "plughw:5,0")

    def test_cards_are_parsed_from_aplay(self):
        self.assertEqual(audio.output_cards(),
                         [(0, "vc4hdmi0 [vc4-hdmi-0]"),
                          (2, "Headphones [bcm2835 Headphones]"),
                          (3, "sndrpihifiberry [snd_rpi_hifiberry_dac]"),
                          (5, "Device [USB Audio Device]")])


class TestCompress(unittest.TestCase):
    """Loudness is a nicety; audible output is the point. A missing or broken
    sox must never silence the box."""

    def test_disabled_returns_input_untouched(self):
        with mock.patch.object(audio.config, "OUTPUT_COMPAND", False):
            self.assertEqual(audio.compress(b"pcm", 22050), b"pcm")

    def test_missing_sox_returns_input_untouched(self):
        with mock.patch.object(audio.config, "OUTPUT_COMPAND", True), \
             mock.patch.object(audio.shutil, "which", return_value=None):
            self.assertEqual(audio.compress(b"pcm", 22050), b"pcm")

    def test_sox_failure_falls_back_to_the_original(self):
        with mock.patch.object(audio.config, "OUTPUT_COMPAND", True), \
             mock.patch.object(audio.shutil, "which", return_value="/usr/bin/sox"), \
             mock.patch.object(audio.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout=b"")):
            self.assertEqual(audio.compress(b"pcm", 22050), b"pcm")

    def test_empty_sox_output_falls_back(self):
        with mock.patch.object(audio.config, "OUTPUT_COMPAND", True), \
             mock.patch.object(audio.shutil, "which", return_value="/usr/bin/sox"), \
             mock.patch.object(audio.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout=b"")):
            self.assertEqual(audio.compress(b"pcm", 22050), b"pcm")

    def test_sox_crash_is_swallowed(self):
        with mock.patch.object(audio.config, "OUTPUT_COMPAND", True), \
             mock.patch.object(audio.shutil, "which", return_value="/usr/bin/sox"), \
             mock.patch.object(audio.subprocess, "run",
                               side_effect=audio.subprocess.TimeoutExpired("sox", 30)):
            self.assertEqual(audio.compress(b"pcm", 22050), b"pcm")

    def test_processed_audio_is_returned_when_sox_succeeds(self):
        with mock.patch.object(audio.config, "OUTPUT_COMPAND", True), \
             mock.patch.object(audio.shutil, "which", return_value="/usr/bin/sox"), \
             mock.patch.object(audio.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout=b"LOUD")):
            self.assertEqual(audio.compress(b"pcm", 22050), b"LOUD")


class TestPlayCommand(unittest.TestCase):
    def test_targets_the_configured_device(self):
        with mock.patch.object(audio, "output_device", return_value="plughw:5,0"), \
             mock.patch.object(audio.config, "PLAY_CMD", ""):
            cmd = audio.play_command(22050)
        self.assertIn("-D", cmd)
        self.assertEqual(cmd[cmd.index("-D") + 1], "plughw:5,0")
        self.assertEqual(cmd[cmd.index("-r") + 1], "22050")

    def test_explicit_play_cmd_overrides_everything(self):
        with mock.patch.object(audio.config, "PLAY_CMD", "paplay --rate={rate}"):
            self.assertEqual(audio.play_command(48000),
                             ["paplay", "--rate=48000"])

    def test_stereo_channel_count_is_passed(self):
        with mock.patch.object(audio.config, "PLAY_CMD", ""), \
             mock.patch.object(audio, "output_device", return_value="default"):
            cmd = audio.play_command(44100, channels=2)
        self.assertEqual(cmd[cmd.index("-c") + 1], "2")


if __name__ == "__main__":
    unittest.main()


class TestPreroll(unittest.TestCase):
    """Recording starts after the wake word fires, so a request run straight
    into "hey jarvis" loses its first syllables ("ask Grok" -> "rock")."""

    def _mic(self, rate=16000):
        with mock.patch.object(audio, "pick_capture_rate", return_value=rate):
            return audio.Microphone(device="x")

    def test_delivered_frames_are_retained(self):
        mic = self._mic()
        frame = b"\x01\x02" * audio.FRAME_SAMPLES
        for _ in range(3):
            mic._q.append(frame)
        gen = mic.frames()
        for _ in range(3):
            next(gen)
        self.assertEqual(mic.preroll(), frame * 3)

    def test_history_is_bounded_by_the_configured_seconds(self):
        mic = self._mic()
        expected = int(audio.config.PREROLL_S * 1000 / audio.config.FRAME_MS)
        self.assertEqual(mic._history.maxlen, expected)

        frame = b"\x00\x00" * audio.FRAME_SAMPLES
        for _ in range(expected + 20):
            mic._q.append(frame)
        gen = mic.frames()
        for _ in range(expected + 20):
            next(gen)
        # older audio is dropped rather than growing without bound
        self.assertEqual(len(mic.preroll()), expected * audio.FRAME_SAMPLES * 2)

    def test_flush_clears_the_preroll_too(self):
        # our own TTS must not be prepended to the next recording
        mic = self._mic()
        mic._q.append(b"\x01\x02" * audio.FRAME_SAMPLES)
        next(mic.frames())
        mic.flush()
        self.assertEqual(mic.preroll(), b"")

    def test_preroll_is_empty_before_any_audio(self):
        self.assertEqual(self._mic().preroll(), b"")
