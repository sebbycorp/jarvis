"""The MCP surface is unauthenticated on the LAN by default — these inputs are
the ones an untrusted caller controls."""
import importlib.util
import unittest
from unittest import mock

import mocks

mocks.install()

import camera  # noqa: E402
import config  # noqa: E402

HAS_FASTMCP = importlib.util.find_spec("fastmcp") is not None


class TestPhotoNames(unittest.TestCase):
    """`take_photo(name)` must not be able to write outside the photo dir."""

    def setUp(self):
        self.cam = camera.Camera()
        mock.patch.object(camera.Camera, "capture_jpeg", return_value=b"jpg").start()
        self.opened = mock.patch("builtins.open", mock.mock_open()).start()
        mock.patch.object(camera.os, "makedirs").start()
        self.addCleanup(mock.patch.stopall)

    def _saved(self, name):
        return self.cam.save_photo(name)

    def test_traversal_is_flattened(self):
        path = self._saved("../../../../etc/cron.d/pwn")
        self.assertTrue(path.startswith(config.PHOTO_DIR))
        self.assertNotIn("..", path)

    def test_absolute_paths_are_flattened(self):
        path = self._saved("/etc/passwd")
        self.assertTrue(path.startswith(config.PHOTO_DIR))

    def test_shell_metacharacters_are_stripped(self):
        path = self._saved("photo; rm -rf ~")
        self.assertNotIn(";", path)
        self.assertRegex(path, r"/[A-Za-z0-9_-]+\.jpg$")

    def test_long_names_are_truncated(self):
        path = self._saved("a" * 500)
        self.assertLessEqual(len(path.rsplit("/", 1)[1]), 64 + len(".jpg"))

    def test_a_name_that_sanitizes_to_nothing_still_gets_a_filename(self):
        path = self._saved("../..")
        self.assertRegex(path, r"/photo_\d+\.jpg$")


@unittest.skipUnless(HAS_FASTMCP, "fastmcp not installed")
class TestAppDirScope(unittest.TestCase):
    """read_file/write_file are scoped to the app dir."""

    def setUp(self):
        import mcp_server
        self.safe_path = mcp_server._safe_path

    def test_escapes_are_refused(self):
        for path in ["../../etc/passwd", "/etc/passwd", "sub/../../../root/.ssh/id_rsa"]:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.safe_path(path)

    def test_paths_inside_the_app_dir_resolve(self):
        self.assertTrue(str(self.safe_path("assistant.py"))
                        .startswith(str(config.APP_DIR)))


if __name__ == "__main__":
    unittest.main()
