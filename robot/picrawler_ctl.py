"""picrawler_ctl — one clean, safe API over the SunFounder PiCrawler stack.

Every front-end (MCP server, web panel, AI assistant, teleop) imports THIS.
It is the single point of hardware access so callers never fight over the HAT.

API facts verified on-device (robot-hat 2.5.x / picrawler 2.1.4 / py3.13):
  - picrawler.Picrawler().do_action(name, step, speed)
  - valid moves: 'forward','backward','turn left','turn right','stand','sit',
    'ready', plus poses: 'wave','push_up','dance','look_up','look_down',
    'look_left','look_right'  (PiCrawler has NO head servo; 'look_*' tilt the
    whole body via the legs).
  - TTS: robot_hat.tts.Espeak().say(text)  (call enable_speaker() once first)
  - camera: vilib.Vilib.camera_start(...); frame = Vilib.img (BGR numpy)
"""
from __future__ import annotations
import os
import time
import threading

# Core movement: our verb -> SunFounder do_action motion name.
ACTION_MAP = {
    "forward": "forward",
    "backward": "backward",
    "turn_left": "turn left",
    "turn_right": "turn right",
    "stand": "stand",
    "rest": "sit",
}

# Extra one-shot poses (fun / expressive). Keys are our names -> motion name.
POSE_MAP = {
    "wave": "wave",
    "push_up": "push_up",
    "dance": "dance",
    "look_up": "look_up",
    "look_down": "look_down",
    "look_left": "look_left",
    "look_right": "look_right",
    "ready": "ready",
}

DEFAULT_SPEED = 80
PHOTO_DIR = os.path.expanduser("~/picrawler-app/photos")

# Battery guard (2S 18650 pack: 8.4V full, 7.4V nominal, ~6.0V empty).
# Servos sag the rail under load; starting a move on a weak pack browns out the
# Pi. Refuse to move below MIN_BATTERY_V; warn below WARN_BATTERY_V.
# Override with env PICRAWLER_MIN_BATTERY_V / PICRAWLER_WARN_BATTERY_V.
MIN_BATTERY_V = float(os.environ.get("PICRAWLER_MIN_BATTERY_V", "6.8"))
WARN_BATTERY_V = float(os.environ.get("PICRAWLER_WARN_BATTERY_V", "7.2"))


class LowBatteryError(RuntimeError):
    """Raised when a movement is refused because the battery is too low."""


class PiCrawlerController:
    """Thread-safe wrapper. Instantiate ONCE per process (use get_controller)."""

    def __init__(self, speed: int = DEFAULT_SPEED, battery_guard: bool = True):
        from picrawler import Picrawler  # lazy: lets non-hw hosts import module
        self._crawler = Picrawler()
        self._lock = threading.Lock()
        self._speed = self._clamp_speed(speed)
        self._camera_on = False
        self._speaker_ready = False
        self._battery_guard = battery_guard
        os.makedirs(PHOTO_DIR, exist_ok=True)

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _clamp_speed(speed: int) -> int:
        return max(1, min(100, int(speed)))

    def set_speed(self, speed: int) -> int:
        with self._lock:
            self._speed = self._clamp_speed(speed)
            return self._speed

    # ---- battery ----------------------------------------------------------
    def battery_voltage(self) -> float:
        """Read pack voltage from the Robot HAT ADC (volts)."""
        from robot_hat import get_battery_voltage
        return round(float(get_battery_voltage()), 2)

    def set_battery_guard(self, enabled: bool) -> dict:
        """Enable/disable the pre-move low-battery guard."""
        self._battery_guard = bool(enabled)
        return {"battery_guard": self._battery_guard}

    def _check_battery(self):
        """Raise LowBatteryError if the pack is too low to move safely."""
        if not self._battery_guard:
            return
        try:
            v = self.battery_voltage()
        except Exception:
            return  # ADC unreadable -> don't block; hardware may differ
        if v < MIN_BATTERY_V:
            raise LowBatteryError(
                f"battery {v}V < {MIN_BATTERY_V}V minimum — refusing to move "
                f"(charge the pack; override with set_battery_guard(False))")

    def _do(self, motion_name: str, steps: int, speed: int | None):
        self._check_battery()
        spd = self._clamp_speed(speed if speed is not None else self._speed)
        steps = max(1, min(20, int(steps)))
        with self._lock:
            self._crawler.do_action(motion_name, steps, spd)
        return {"action": motion_name, "steps": steps, "speed": spd}

    # ---- movement ---------------------------------------------------------
    def forward(self, steps: int = 1, speed: int | None = None):
        return self._do(ACTION_MAP["forward"], steps, speed)

    def backward(self, steps: int = 1, speed: int | None = None):
        return self._do(ACTION_MAP["backward"], steps, speed)

    def turn_left(self, steps: int = 1, speed: int | None = None):
        return self._do(ACTION_MAP["turn_left"], steps, speed)

    def turn_right(self, steps: int = 1, speed: int | None = None):
        return self._do(ACTION_MAP["turn_right"], steps, speed)

    def stand(self, speed: int | None = None):
        return self._do(ACTION_MAP["stand"], 1, speed)

    def rest(self, speed: int | None = None):
        return self._do(ACTION_MAP["rest"], 1, speed)

    def pose(self, name: str, speed: int | None = None):
        """Run an expressive one-shot pose (see POSE_MAP)."""
        if name not in POSE_MAP:
            raise ValueError(f"unknown pose {name!r}; valid: {sorted(POSE_MAP)}")
        return self._do(POSE_MAP[name], 1, speed)

    def stop(self):
        """Leave the robot in a safe standing pose."""
        return self._do(ACTION_MAP["stand"], 1, self._speed)

    # ---- calibration ------------------------------------------------------
    def set_leg_offset(self, index: int, offset: float) -> dict:
        """Set one servo trim offset (index 0-11, clamped +/-20) and persist it.

        picrawler stores 12 offsets and persists them via set_offset(list).
        We read the current 12, patch one, and re-apply.
        """
        index = int(index)
        if not 0 <= index <= 11:
            raise ValueError("leg servo index must be 0-11")
        with self._lock:
            current = list(getattr(self._crawler, "offset", [0.0] * 12))
            current[index] = max(-20.0, min(20.0, float(offset)))
            self._crawler.set_offset(current)
        return {"index": index, "offset": current[index], "offsets": current}

    def get_offsets(self) -> list:
        with self._lock:
            return list(getattr(self._crawler, "offset", [0.0] * 12))

    # ---- camera -----------------------------------------------------------
    def _ensure_camera(self):
        if not self._camera_on:
            from vilib import Vilib
            Vilib.camera_start(vflip=False, hflip=False)
            Vilib.display(local=False, web=False)
            time.sleep(1.2)  # let the first frames arrive
            self._camera_on = True

    def photo(self, name: str | None = None) -> str:
        from vilib import Vilib
        self._ensure_camera()
        name = name or f"photo_{int(time.time())}"
        Vilib.take_photo(name, PHOTO_DIR)
        time.sleep(0.3)
        return os.path.join(PHOTO_DIR, f"{name}.jpg")

    def capture_jpeg_bytes(self) -> bytes:
        """Return the latest camera frame as JPEG bytes (MJPEG / MCP vision)."""
        import cv2
        from vilib import Vilib
        self._ensure_camera()
        frame = Vilib.img  # BGR numpy array maintained by vilib
        if frame is None:
            raise RuntimeError("no camera frame available yet")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("failed to encode camera frame")
        return buf.tobytes()

    # ---- audio ------------------------------------------------------------
    def speak(self, text: str) -> dict:
        from robot_hat.tts import Espeak, enable_speaker
        with self._lock:
            if not self._speaker_ready:
                try:
                    enable_speaker()
                except Exception:
                    pass
                self._speaker_ready = True
            Espeak().say(text)
        return {"spoke": text}

    # ---- status -----------------------------------------------------------
    def status(self) -> dict:
        st = {"speed": self._speed, "camera_on": self._camera_on,
              "speaker_ready": self._speaker_ready,
              "battery_guard": self._battery_guard,
              "min_battery_v": MIN_BATTERY_V}
        try:
            v = self.battery_voltage()
            st["battery_v"] = v
            st["battery_ok"] = v >= MIN_BATTERY_V
            st["battery_low_warn"] = v < WARN_BATTERY_V
        except Exception as e:
            st["battery_v"] = None
            st["battery_error"] = str(e)
        return st


# Module-level singleton accessor
_controller: PiCrawlerController | None = None
_controller_lock = threading.Lock()


def get_controller() -> PiCrawlerController:
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = PiCrawlerController()
        return _controller
