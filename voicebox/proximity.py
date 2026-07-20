"""Ultrasonic range finder — wave your hand to start a turn.

The HC-SR04 on the Robot HAT (trig D2 / echo D3) gives a physical trigger that
background audio cannot fake, which is what kept falsely waking the box. It
polls on a background thread and sets an Event the assistant loop watches
alongside the wake word.

Readings of -1 mean "no echo came back" — out of range, or the pulse hit
something at a bad angle. They are treated as far away, never as a trigger.
"""
from __future__ import annotations
import threading
import time

import config


class Ranger:
    """Background distance poller with hand-wave detection."""

    def __init__(self):
        self._sensor = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.triggered = threading.Event()
        self.available = False
        self.last_cm: float = -1.0
        self._error: str | None = None

    # ---- hardware ----------------------------------------------------------
    def open(self) -> bool:
        if self._sensor is not None:
            return True
        if not config.PROXIMITY_ENABLED:
            self._error = "disabled (VOICEBOX_PROXIMITY_ENABLED=0)"
            return False
        try:
            from robot_hat import Pin, Ultrasonic
            self._sensor = Ultrasonic(Pin(config.PROXIMITY_TRIG),
                                      Pin(config.PROXIMITY_ECHO))
            self.available = True
            return True
        except Exception as e:
            self._error = str(e)
            return False

    @property
    def error(self) -> str | None:
        return self._error

    def read(self) -> float:
        """One distance reading in cm, or -1 when nothing echoed back."""
        if self._sensor is None and not self.open():
            return -1.0
        try:
            value = float(self._sensor.read())
        except Exception:
            return -1.0
        self.last_cm = value
        return value

    # ---- wave detection ----------------------------------------------------
    def _poll(self) -> None:
        last_fire = 0.0
        close_streak = 0
        while not self._stop.is_set():
            cm = self.read()
            near = 0 < cm <= config.WAVE_CM
            # Require consecutive close readings: a single stray sample (echo
            # off furniture, or a servo-era wire in the beam) shouldn't wake it.
            close_streak = close_streak + 1 if near else 0
            now = time.monotonic()
            if (close_streak >= config.WAVE_SAMPLES
                    and now - last_fire >= config.WAVE_COOLDOWN_S):
                last_fire = now
                close_streak = 0
                self.triggered.set()
            self._stop.wait(config.PROXIMITY_POLL_S)

    def start(self) -> bool:
        if not self.open():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def clear(self) -> None:
        self.triggered.clear()


_ranger: Ranger | None = None
_lock = threading.Lock()


def get_ranger() -> Ranger:
    global _ranger
    with _lock:
        if _ranger is None:
            _ranger = Ranger()
    return _ranger
