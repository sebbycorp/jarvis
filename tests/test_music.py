"""Library search — the query arrives from speech recognition, so it is fuzzy."""
import unittest
from unittest import mock

import mocks

mocks.install()

import music  # noqa: E402

LIBRARY = [
    "/music/Pink Floyd - Comfortably Numb.mp3",
    "/music/Daft Punk/Around the World.flac",
    "/music/classical/Bach_Cello_Suite_No1.wav",
    "/music/Miles Davis - So What.m4a",
]


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.player = music.Player()
        patcher = mock.patch.object(music.Player, "library", return_value=LIBRARY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_matches_ignoring_case(self):
        self.assertEqual(self.player.search("comfortably numb"),
                         [LIBRARY[0]])

    def test_matches_across_separators(self):
        # STT gives "bach cello suite" for a file named Bach_Cello_Suite_No1
        self.assertEqual(self.player.search("bach cello suite"), [LIBRARY[2]])

    def test_all_terms_must_appear(self):
        self.assertEqual(self.player.search("miles davis so what"), [LIBRARY[3]])
        self.assertEqual(self.player.search("miles davis kind of blue"), [])

    def test_partial_artist_matches_their_tracks(self):
        self.assertEqual(self.player.search("daft punk"), [LIBRARY[1]])

    def test_empty_query_matches_nothing(self):
        self.assertEqual(self.player.search("  "), [])

    def test_punctuation_in_the_query_is_ignored(self):
        self.assertEqual(self.player.search("So What?"), [LIBRARY[3]])


class TestPlay(unittest.TestCase):
    def setUp(self):
        self.player = music.Player()
        mock.patch.object(music.Player, "library", return_value=LIBRARY).start()
        self.addCleanup(mock.patch.stopall)
        self.started = mock.patch.object(music.Player, "_start").start()

    def test_play_queues_the_remaining_tracks(self):
        r = self.player.play()
        self.assertEqual(r["queued"], len(LIBRARY) - 1)
        self.started.assert_called_once()

    def test_play_with_no_match_reports_an_error(self):
        r = self.player.play("nonexistent song")
        self.assertIn("nothing found", r["error"])
        self.started.assert_not_called()

    def test_empty_library_reports_an_error(self):
        with mock.patch.object(music.Player, "library", return_value=[]):
            self.assertIn("no music", self.player.play()["error"])


# What this Pi actually reports. The speaker's softvol lives on card 3; card 0
# has the unrelated Master/Capture that bare `amixer scontrols` would return.
CARDS = {
    0: "Simple mixer control 'Master',0\nSimple mixer control 'Capture',0\n",
    3: "Simple mixer control 'robot-hat speaker',0\n",
    4: "Simple mixer control 'Mic',0\n",
}


class TestVolume(unittest.TestCase):
    def setUp(self):
        mock.patch.object(music.shutil, "which",
                          return_value="/usr/bin/amixer").start()
        self.run = mock.patch.object(music.subprocess, "run").start()
        self.run.side_effect = self._amixer
        self.addCleanup(mock.patch.stopall)

    @staticmethod
    def _amixer(argv, **kwargs):
        card = int(argv[2])
        if argv[3] == "scontrols":
            return mock.Mock(returncode=0, stdout=CARDS.get(card, ""))
        return mock.Mock(returncode=0 if card in CARDS else 1, stdout="")

    def _sset_calls(self):
        return [c.args[0] for c in self.run.call_args_list if "sset" in c.args[0]]

    def test_clamped_to_0_100(self):
        self.assertEqual(music.Player.set_volume(500)["volume"], 100)
        self.assertEqual(music.Player.set_volume(-20)["volume"], 0)

    def test_targets_the_hat_softvol_on_its_own_card(self):
        r = music.Player.set_volume(45)
        self.assertEqual(r["control"], "robot-hat speaker")
        # the bug this guards: card 0's "Master" is a different device
        self.assertEqual(r["card"], 3)

    def test_discovers_controls_across_all_cards(self):
        found = music.Player.mixer_controls()
        self.assertIn((3, "robot-hat speaker"), found)
        self.assertIn((0, "Master"), found)

    def test_never_turns_the_microphone_down(self):
        music.Player.set_volume(10)
        for argv in self._sset_calls():
            self.assertNotIn("Mic", argv)
            self.assertNotIn("Capture", argv)

    def test_no_controls_reports_an_error(self):
        self.run.side_effect = lambda argv, **k: mock.Mock(returncode=1, stdout="")
        self.assertIn("error", music.Player.set_volume(50))

    def test_missing_amixer_reports_an_error(self):
        with mock.patch.object(music.shutil, "which", return_value=None):
            self.assertIn("error", music.Player.set_volume(50))


if __name__ == "__main__":
    unittest.main()
