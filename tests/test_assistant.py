"""Intent routing in the voice loop: what gets handled locally vs sent to a model."""
import unittest
from unittest import mock

import mocks

mocks.install()

import assistant  # noqa: E402
import llm  # noqa: E402


class FakePlayer:
    def __init__(self, playing=False):
        self.is_playing = playing
        self.calls = []
        self.volume = 70

    def play(self, query=None, shuffle=False):
        self.calls.append(("play", query, shuffle))
        return {"playing": "song.mp3", "queued": 0}

    def stop(self):
        self.calls.append(("stop",))
        return {"playing": None}

    def skip(self):
        self.calls.append(("skip",))
        return {"playing": "next.mp3"}

    def set_volume(self, percent):
        self.calls.append(("volume", percent))
        self.volume = percent
        return {"volume": percent}


class FakeRouter:
    def __init__(self, reply="model reply"):
        self.reply = reply
        self.asked = []
        self.was_reset = False

    def ask(self, text, image_jpeg=None, backend=None, remember=True):
        self.asked.append((text, image_jpeg))
        return {"backend": "local", "text": text, "reply": self.reply,
                "switched": False, "saw_image": bool(image_jpeg)}

    def reset(self):
        self.was_reset = True

    def label(self, backend=None):
        return "local Qwen"


def make_assistant(playing=False, reply="model reply"):
    a = assistant.Assistant.__new__(assistant.Assistant)
    a.router = FakeRouter(reply)
    a.player = FakePlayer(playing)
    a._volume = 70
    return a


class TestMusicIntents(unittest.TestCase):
    def test_play_with_a_title_searches_for_it(self):
        a = make_assistant()
        self.assertIn("Playing", a.handle("play bohemian rhapsody"))
        self.assertEqual(a.player.calls[0][:2], ("play", "bohemian rhapsody"))
        self.assertEqual(a.router.asked, [])  # never reached the model

    def test_play_music_with_no_title_shuffles_everything(self):
        a = make_assistant()
        a.handle("play some music")
        action, query, shuffle = a.player.calls[0]
        self.assertEqual((action, query), ("play", None))
        self.assertTrue(shuffle)

    def test_stop_only_applies_while_playing(self):
        a = make_assistant(playing=True)
        self.assertEqual(a.handle("stop the music"), "Stopped.")
        self.assertEqual(a.player.calls, [("stop",)])

        idle = make_assistant(playing=False)
        idle.handle("stop the music")
        self.assertEqual(idle.player.calls, [])   # nothing playing
        self.assertEqual(len(idle.router.asked), 1)  # so the model answers

    def test_skip_advances_the_queue(self):
        a = make_assistant(playing=True)
        self.assertIn("next.mp3", a.handle("next track"))
        self.assertEqual(a.player.calls, [("skip",)])

    def test_a_visual_question_is_not_mistaken_for_playback(self):
        a = make_assistant()
        with mock.patch.object(assistant, "camera_frame", return_value=b"jpg"):
            a.handle("what do you see in front of you")
        self.assertEqual(a.player.calls, [])
        self.assertEqual(len(a.router.asked), 1)


class TestVolume(unittest.TestCase):
    def test_absolute_volume(self):
        a = make_assistant()
        self.assertIn("40", a.handle("set volume to 40"))
        self.assertEqual(a.player.calls, [("volume", 40)])

    def test_relative_volume_steps_and_clamps(self):
        a = make_assistant()
        a.handle("volume up")
        self.assertEqual(a.player.calls[-1], ("volume", 85))
        a.handle("volume up")
        self.assertEqual(a.player.calls[-1], ("volume", 100))  # clamped
        for _ in range(10):
            a.handle("volume down")
        self.assertEqual(a.player.calls[-1], ("volume", 0))    # clamped


class TestConversation(unittest.TestCase):
    def test_reset_clears_history_locally(self):
        a = make_assistant()
        self.assertIn("forgotten", a.handle("forget our conversation"))
        self.assertTrue(a.router.was_reset)
        self.assertEqual(a.router.asked, [])

    def test_plain_question_goes_to_the_model_without_a_frame(self):
        a = make_assistant(reply="Lima.")
        self.assertEqual(a.handle("what is the capital of peru"), "Lima.")
        self.assertEqual(a.router.asked, [("what is the capital of peru", None)])

    def test_visual_question_attaches_a_camera_frame(self):
        a = make_assistant(reply="A mug.")
        with mock.patch.object(assistant, "camera_frame", return_value=b"jpeg"):
            self.assertEqual(a.handle("what am I holding here"), "A mug.")
        self.assertEqual(a.router.asked[0][1], b"jpeg")

    def test_camera_failure_still_answers(self):
        a = make_assistant(reply="Can't tell.")
        with mock.patch.object(assistant, "camera_frame",
                               side_effect=RuntimeError("no camera")):
            self.assertEqual(a.handle("what do you see"), "Can't tell.")
        self.assertIsNone(a.router.asked[0][1])

    def test_gateway_failure_is_spoken_not_raised(self):
        a = make_assistant()
        a.router.ask = mock.Mock(side_effect=llm.LLMError("gateway down"))
        self.assertIn("couldn't reach", a.handle("hello"))

    def test_empty_input_produces_no_speech(self):
        self.assertEqual(make_assistant().handle("   "), "")


if __name__ == "__main__":
    unittest.main()
