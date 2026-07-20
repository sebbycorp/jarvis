"""Camera streaming + motion detection, in a container.

The camera has exactly one owner, and the voice assistant also wants it for
vision questions. So this holds the camera only while someone is watching:
the first viewer starts it, the last one to leave (plus a grace period) hands
it back. The assistant releases it after a few idle seconds in turn, so the two
take turns rather than fighting.

    GET /              viewer page
    GET /stream.mjpg   live MJPEG
    GET /snapshot.jpg  single frame
    GET /api/status    camera + motion state
    GET /api/motion    recent motion events
"""
from __future__ import annotations
import io
import os
import threading
import time
from collections import deque

import numpy as np
from flask import Flask, Response, jsonify, render_template

WIDTH = int(os.environ.get("CAM_WIDTH", "1280"))
HEIGHT = int(os.environ.get("CAM_HEIGHT", "720"))
FPS = float(os.environ.get("CAM_FPS", "10"))
IDLE_RELEASE_S = float(os.environ.get("CAM_IDLE_RELEASE_S", "5"))
# Mean absolute pixel difference on a downscaled grey frame. Tuned for "a
# person moved", not "a cloud passed" — raise it if the log fills with noise.
MOTION_THRESHOLD = float(os.environ.get("CAM_MOTION_THRESHOLD", "6.0"))
MOTION_COOLDOWN_S = float(os.environ.get("CAM_MOTION_COOLDOWN_S", "5"))

app = Flask(__name__)


class CameraStream:
    """Owns the camera while at least one viewer is connected."""

    def __init__(self):
        self._cam = None
        self._lock = threading.Lock()
        self._viewers = 0
        self._frame: bytes | None = None
        self._frame_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._release_timer: threading.Timer | None = None
        self.error: str | None = None
        self.motion_events: deque = deque(maxlen=50)
        self.motion_score = 0.0
        self._prev_small: np.ndarray | None = None
        self._last_motion = 0.0

    # ---- ownership ---------------------------------------------------------
    def _start_locked(self) -> bool:
        if self._cam is not None:
            return True
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            cam.configure(cam.create_video_configuration(
                main={"size": (WIDTH, HEIGHT), "format": "RGB888"}))
            cam.start()
            time.sleep(1.0)
            self._cam = cam
            self.error = None
        except Exception as e:
            # Almost always "device busy": the assistant has it for a vision
            # question and will release it shortly.
            self.error = str(e)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    def acquire(self) -> bool:
        with self._lock:
            if self._release_timer is not None:
                self._release_timer.cancel()
                self._release_timer = None
            self._viewers += 1
            return self._start_locked()

    def release(self) -> None:
        with self._lock:
            self._viewers = max(0, self._viewers - 1)
            if self._viewers:
                return
            # Grace period: a browser reconnecting between frames shouldn't
            # cause a full camera stop/start cycle.
            self._release_timer = threading.Timer(IDLE_RELEASE_S, self._shutdown)
            self._release_timer.daemon = True
            self._release_timer.start()

    def _shutdown(self) -> None:
        with self._lock:
            if self._viewers:
                return
            self._stop.set()
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)
        with self._lock:
            if self._cam is not None:
                try:
                    self._cam.stop()
                    self._cam.close()
                finally:
                    self._cam = None
            self._frame = None
            self._prev_small = None

    # ---- capture -----------------------------------------------------------
    def _capture_loop(self) -> None:
        import cv2
        interval = 1.0 / max(FPS, 1.0)
        while not self._stop.is_set():
            start = time.monotonic()
            try:
                frame = self._cam.capture_array()
            except Exception as e:
                self.error = str(e)
                break
            self._detect_motion(frame)
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                self._frame = buf.tobytes()
                self._frame_at = time.time()
            elapsed = time.monotonic() - start
            self._stop.wait(max(0.0, interval - elapsed))

    def _detect_motion(self, frame: np.ndarray) -> None:
        # Downscale hard before comparing: cheap, and it ignores sensor noise
        # and tiny detail that would otherwise read as movement.
        small = frame[::16, ::16].mean(axis=2).astype(np.float32)
        if self._prev_small is not None and small.shape == self._prev_small.shape:
            self.motion_score = float(np.abs(small - self._prev_small).mean())
            now = time.time()
            if (self.motion_score >= MOTION_THRESHOLD
                    and now - self._last_motion >= MOTION_COOLDOWN_S):
                self._last_motion = now
                self.motion_events.appendleft(
                    {"at": now, "score": round(self.motion_score, 2)})
        self._prev_small = small

    # ---- readers -----------------------------------------------------------
    def frame(self) -> bytes | None:
        return self._frame

    def snapshot(self, timeout: float = 6.0) -> bytes | None:
        """One frame, starting the camera if nobody is watching."""
        self.acquire()
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._frame is not None:
                    return self._frame
                time.sleep(0.1)
            return None
        finally:
            self.release()

    def status(self) -> dict:
        return {
            "running": self._cam is not None,
            "viewers": self._viewers,
            "size": [WIDTH, HEIGHT],
            "fps": FPS,
            "frame_age": round(time.time() - self._frame_at, 1) if self._frame_at else None,
            "motion_score": round(self.motion_score, 2),
            "motion_threshold": MOTION_THRESHOLD,
            "motion_events": len(self.motion_events),
            "error": self.error,
        }


stream = CameraStream()


@app.get("/")
def index():
    return render_template("index.html", width=WIDTH, height=HEIGHT)


@app.get("/stream.mjpg")
def mjpg():
    def generate():
        stream.acquire()
        try:
            last = 0.0
            while True:
                frame = stream.frame()
                if frame is None or stream._frame_at == last:
                    time.sleep(0.03)
                    if stream.error and frame is None:
                        break
                    continue
                last = stream._frame_at
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(frame)).encode() +
                       b"\r\n\r\n" + frame + b"\r\n")
        finally:
            stream.release()

    return Response(generate(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/snapshot.jpg")
def snapshot():
    frame = stream.snapshot()
    if frame is None:
        return jsonify({"error": stream.error or "no frame available"}), 503
    return Response(frame, mimetype="image/jpeg")


@app.get("/api/status")
def status():
    return jsonify(stream.status())


@app.get("/api/motion")
def motion():
    return jsonify({"events": list(stream.motion_events)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
            threaded=True)
