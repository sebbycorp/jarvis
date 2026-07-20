"""A faulty CSI cable makes libcamera block forever rather than raise — the
camera must never be able to stall the voice loop."""
import threading
import unittest
from unittest import mock

import mocks

mocks.install()

import camera  # noqa: E402


class TestTimeout(unittest.TestCase):
    def setUp(self):
        self.cam = camera.Camera()

    def test_a_hanging_open_raises_instead_of_blocking(self):
        started = threading.Event()

        def hang():
            started.set()
            threading.Event().wait()  # never returns, like a wedged libcamera

        with mock.patch.object(camera.Camera, "_open", side_effect=hang):
            with self.assertRaisesRegex(camera.CameraError, "timed out"):
                self.cam._run(self.cam._open, timeout=0.2)
        self.assertTrue(started.is_set())

    def test_the_cable_is_named_in_the_error(self):
        with mock.patch.object(camera.Camera, "_open",
                               side_effect=lambda: threading.Event().wait()):
            with self.assertRaisesRegex(camera.CameraError, "ribbon cable"):
                self.cam._run(self.cam._open, timeout=0.1)

    def test_one_failure_marks_the_camera_broken_and_fails_fast(self):
        with mock.patch.object(camera.Camera, "_open",
                               side_effect=lambda: threading.Event().wait()):
            with self.assertRaises(camera.CameraError):
                self.cam._run(self.cam._open, timeout=0.1)
        self.assertIsNotNone(self.cam.broken)

        # a second attempt must not wait again
        opener = mock.Mock()
        with mock.patch.object(camera.Camera, "_open", opener):
            with self.assertRaises(camera.CameraError):
                self.cam._ensure()
        opener.assert_not_called()

    def test_an_exception_propagates_as_camera_error(self):
        with mock.patch.object(camera.Camera, "_open",
                               side_effect=RuntimeError("no sensor")):
            with self.assertRaisesRegex(camera.CameraError, "no sensor"):
                self.cam._ensure()

    def test_a_working_capture_returns_its_value(self):
        self.assertEqual(self.cam._run(lambda: b"jpeg", timeout=5), b"jpeg")

    def test_disabled_camera_reports_clearly(self):
        with mock.patch.object(camera.config, "CAMERA_ENABLED", False):
            with self.assertRaisesRegex(camera.CameraError, "disabled"):
                camera.Camera()._ensure()


if __name__ == "__main__":
    unittest.main()
