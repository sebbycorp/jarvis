"""Off-robot unit tests for picrawler_ctl (stdlib unittest, no external deps).

Run:  python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "robot"))

import hwmocks  # noqa: E402
hwmocks.install()  # must precede importing picrawler_ctl

import picrawler_ctl as pc  # noqa: E402


def fresh_controller(**kw):
    """New controller with a fresh fake crawler and reset mock state."""
    hwmocks.FakePicrawler.instances.clear()
    hwmocks.FakeEspeak.said.clear()
    hwmocks.FakeVilib.photos.clear()
    hwmocks.set_battery(8.0)
    # bypass the module singleton so each test is isolated
    return pc.PiCrawlerController(**kw)


class TestMovement(unittest.TestCase):
    def test_action_map_uses_correct_motion_names(self):
        c = fresh_controller()
        c.forward(); c.backward(); c.turn_left(); c.turn_right()
        c.stand(); c.rest()
        names = [call[0] for call in hwmocks.FakePicrawler.instances[0].calls]
        self.assertEqual(
            names,
            ["forward", "backward", "turn left", "turn right", "stand", "sit"])

    def test_speed_is_clamped(self):
        c = fresh_controller()
        c.forward(speed=999)
        c.forward(speed=-5)
        speeds = [call[2] for call in hwmocks.FakePicrawler.instances[0].calls]
        self.assertEqual(speeds, [100, 1])

    def test_steps_clamped_and_passed(self):
        c = fresh_controller()
        c.forward(steps=99)
        self.assertEqual(hwmocks.FakePicrawler.instances[0].calls[0][1], 20)

    def test_pose_valid_and_invalid(self):
        c = fresh_controller()
        c.pose("wave")
        self.assertEqual(hwmocks.FakePicrawler.instances[0].calls[0][0], "wave")
        with self.assertRaises(ValueError):
            c.pose("moonwalk")

    def test_stop_stands(self):
        c = fresh_controller()
        c.stop()
        self.assertEqual(hwmocks.FakePicrawler.instances[0].calls[0][0], "stand")


class TestBatteryGuard(unittest.TestCase):
    def test_refuses_move_when_low(self):
        c = fresh_controller()
        hwmocks.set_battery(6.0)  # below default 6.8 minimum
        with self.assertRaises(pc.LowBatteryError):
            c.forward()
        # no do_action should have been issued
        self.assertEqual(hwmocks.FakePicrawler.instances[0].calls, [])

    def test_allows_move_when_ok(self):
        c = fresh_controller()
        hwmocks.set_battery(7.6)
        c.forward()
        self.assertEqual(len(hwmocks.FakePicrawler.instances[0].calls), 1)

    def test_guard_can_be_disabled(self):
        c = fresh_controller()
        hwmocks.set_battery(6.0)
        c.set_battery_guard(False)
        c.forward()  # should not raise
        self.assertEqual(len(hwmocks.FakePicrawler.instances[0].calls), 1)

    def test_status_reports_battery(self):
        c = fresh_controller()
        hwmocks.set_battery(7.4)
        st = c.status()
        self.assertEqual(st["battery_v"], 7.4)
        self.assertTrue(st["battery_ok"])


class TestSanitization(unittest.TestCase):
    def test_speak_strips_shell_metacharacters(self):
        c = fresh_controller()
        out = c.speak('hello; rm -rf / `whoami` $(id) & echo "hi"')
        spoken = hwmocks.FakeEspeak.said[-1]
        for bad in ["`", "$", ";", "|", "&", "<", ">", '"', "'", "(", ")"]:
            self.assertNotIn(bad, spoken)
        self.assertEqual(spoken, out["spoke"])
        self.assertIn("hello", spoken)

    def test_photo_name_cannot_escape_dir(self):
        c = fresh_controller()
        path = c.photo("../../etc/passwd")
        # sanitized name has no path separators or dots-as-traversal
        name_arg = hwmocks.FakeVilib.photos[-1][0]
        self.assertNotIn("/", name_arg)
        self.assertNotIn("..", name_arg)
        self.assertTrue(path.endswith(".jpg"))


class TestCalibration(unittest.TestCase):
    def test_set_leg_offset_persists_and_clamps(self):
        c = fresh_controller()
        r = c.set_leg_offset(2, 50)  # clamps to +20
        self.assertEqual(r["offset"], 20.0)
        self.assertEqual(hwmocks.FakePicrawler.instances[0].offset[2], 20.0)
        with self.assertRaises(ValueError):
            c.set_leg_offset(99, 0)


class TestCamera(unittest.TestCase):
    def test_capture_returns_bytes(self):
        c = fresh_controller()
        hwmocks.FakeVilib.img = object()  # non-None triggers encode path
        data = c.capture_jpeg_bytes()
        self.assertIsInstance(data, (bytes, bytearray))
        self.assertTrue(len(data) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
