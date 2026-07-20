"""Hand-wave triggering.

The point of the ultrasonic trigger is that background audio can't fake it —
the TV repeatedly tripped the wake word. So the bar here is: no spurious fires,
and never a crash when the sensor is absent or flaky.
"""
import threading
import unittest
from unittest import mock

import mocks

mocks.install()

import config  # noqa: E402
import proximity  # noqa: E402


class TestRead(unittest.TestCase):
    def setUp(self):
        self.r = proximity.Ranger()
        self.r._sensor = mock.Mock()

    def test_a_reading_is_returned_and_remembered(self):
        self.r._sensor.read.return_value = 51.5
        self.assertEqual(self.r.read(), 51.5)
        self.assertEqual(self.r.last_cm, 51.5)

    def test_a_sensor_error_reads_as_no_echo(self):
        self.r._sensor.read.side_effect = OSError("i2c blip")
        self.assertEqual(self.r.read(), -1.0)

    def test_missing_sensor_reads_as_no_echo(self):
        r = proximity.Ranger()
        with mock.patch.object(proximity.Ranger, "open", return_value=False):
            self.assertEqual(r.read(), -1.0)


class TestWaveDetection(unittest.TestCase):
    """_poll drives the trigger; feed it scripted readings."""

    def _run_poll(self, readings, samples=2):
        r = proximity.Ranger()
        r._sensor = mock.Mock()
        seq = list(readings)

        def read():
            if not seq:
                r._stop.set()
                return -1.0
            return seq.pop(0)

        r._sensor.read.side_effect = read
        with mock.patch.object(config, "WAVE_SAMPLES", samples), \
             mock.patch.object(config, "PROXIMITY_POLL_S", 0), \
             mock.patch.object(config, "WAVE_CM", 20.0), \
             mock.patch.object(config, "WAVE_COOLDOWN_S", 0):
            r._poll()
        return r

    def test_a_hand_held_close_triggers(self):
        self.assertTrue(self._run_poll([10.0, 10.0]).triggered.is_set())

    def test_an_empty_room_never_triggers(self):
        self.assertFalse(self._run_poll([51.5] * 20).triggered.is_set())

    def test_a_single_stray_reading_does_not_trigger(self):
        # one bad sample (echo off furniture) must not wake the box
        self.assertFalse(
            self._run_poll([51.5, 10.0, 51.5, 51.5]).triggered.is_set())

    def test_no_echo_is_treated_as_far_away(self):
        # -1 means nothing came back; it is not "distance zero"
        self.assertFalse(self._run_poll([-1.0] * 10).triggered.is_set())

    def test_readings_beyond_the_threshold_do_not_trigger(self):
        self.assertFalse(self._run_poll([25.0, 25.0, 30.0]).triggered.is_set())


class TestLifecycle(unittest.TestCase):
    def test_start_without_hardware_reports_false(self):
        r = proximity.Ranger()
        with mock.patch.object(proximity.Ranger, "open", return_value=False):
            self.assertFalse(r.start())

    def test_disabled_by_config(self):
        r = proximity.Ranger()
        with mock.patch.object(config, "PROXIMITY_ENABLED", False):
            self.assertFalse(r.open())
        self.assertIn("disabled", r.error)

    def test_clear_resets_the_trigger(self):
        # a stale wave from the previous turn must not fire the next one
        r = proximity.Ranger()
        r.triggered.set()
        r.clear()
        self.assertFalse(r.triggered.is_set())

    def test_trigger_is_an_event_the_wake_loop_can_watch(self):
        self.assertIsInstance(proximity.Ranger().triggered, threading.Event)


if __name__ == "__main__":
    unittest.main()
