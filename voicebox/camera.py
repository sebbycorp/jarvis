"""Camera capture via picamera2.

The old stack went through SunFounder's vilib, which pulled in mediapipe for
local recognition — unavailable on this Pi's Python 3.13. The voice box only
needs still frames to hand to a vision model, so it talks to picamera2 directly.
"""
from __future__ import annotations
import io
import os
import re
import threading
import time

import config

# With a loose/faulty CSI ribbon, libcamera logs "Camera frontend has timed out"
# and then *blocks forever* instead of raising — it hung preflight indefinitely.
# Every camera entry point is therefore time-bounded, and one failure disables
# the camera for the process so a broken cable can't stall every vision turn.
CAMERA_TIMEOUT_S = 20.0


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(self):
        self._cam = None
        self._lock = threading.Lock()
        self._broken: str | None = None
        self._idle_timer: threading.Timer | None = None

    def _open(self):
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_still_configuration(main={"size": (1280, 720)}))
        cam.start()
        time.sleep(1.5)  # auto-exposure/white-balance settle
        return cam

    def _ensure(self):
        if self._cam is not None:
            return self._cam
        with self._lock:
            if self._cam is not None:
                return self._cam
            if self._broken:
                raise CameraError(self._broken)
            if not config.CAMERA_ENABLED:
                raise CameraError("camera disabled (VOICEBOX_CAMERA_ENABLED=0)")
            self._cam = self._run(self._open)
            return self._cam

    def _run(self, fn, timeout: float = CAMERA_TIMEOUT_S):
        """Run `fn` on a worker thread, giving up after `timeout`.

        A hung libcamera call cannot be interrupted, so the thread is left as a
        daemon and the camera is marked broken — the box keeps answering
        everything that isn't a vision question.
        """
        box: dict = {}

        def target():
            try:
                box["value"] = fn()
            except BaseException as e:  # noqa: BLE001 - reported to the caller
                box["error"] = e

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self._broken = (f"camera timed out after {timeout:.0f}s — check the "
                            "CSI ribbon cable is seated at both ends")
            raise CameraError(self._broken)
        if "error" in box:
            message = f"camera unavailable: {box['error']}"
            # "device busy" means another process (the streaming container)
            # holds it right now — transient, so don't latch it as broken
            if "busy" not in str(box["error"]).lower():
                self._broken = message
            raise CameraError(message) from box["error"]
        return box["value"]

    @property
    def started(self) -> bool:
        return self._cam is not None

    @property
    def broken(self) -> str | None:
        return self._broken

    def capture_jpeg(self) -> bytes:
        cam = self._ensure()

        def grab() -> bytes:
            buf = io.BytesIO()
            cam.capture_file(buf, format="jpeg")
            return buf.getvalue()

        try:
            with self._lock:
                return self._run(grab)
        finally:
            self._schedule_release()

    def _schedule_release(self) -> None:
        """Hand the camera back after a quiet spell so another process (the
        streaming container) can take it."""
        if config.CAMERA_IDLE_S <= 0:
            return
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(config.CAMERA_IDLE_S, self.close)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def save_photo(self, name: str | None = None) -> str:
        os.makedirs(config.PHOTO_DIR, exist_ok=True)
        name = name or f"photo_{int(time.time())}"
        # Strip path components and unsafe chars: `name` reaches here from an
        # unauthenticated MCP tool and must not escape PHOTO_DIR.
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(name))[:64]
        # "../.." sanitizes to "_____" — technically safe, but a useless
        # filename, so treat anything with no alphanumerics as unnamed.
        if not re.search(r"[A-Za-z0-9]", name):
            name = f"photo_{int(time.time())}"
        path = os.path.join(config.PHOTO_DIR, f"{name}.jpg")
        with open(path, "wb") as f:
            f.write(self.capture_jpeg())
        return path

    def close(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None
        with self._lock:
            if self._cam is not None:
                try:
                    self._cam.stop()
                    self._cam.close()
                finally:
                    self._cam = None


_camera: Camera | None = None
_lock = threading.Lock()


def get_camera() -> Camera:
    global _camera
    with _lock:
        if _camera is None:
            _camera = Camera()
    return _camera
