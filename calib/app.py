"""PiCrawler web calibration tool (Dockerized).

Sliders for each of the 12 servo offsets (+/-20 deg). Adjusting a slider updates
that offset, re-applies the neutral stance so you can see the leg move, and
persists to the picrawler config immediately. Runs in a container with the
Robot HAT reachable over /dev/i2c-1; offsets are written to the mounted
~/.config/.picrawler.config so the rest of the stack (MCP server, etc.) sees them.
"""
import os
import threading
from flask import Flask, render_template, jsonify, request

from picrawler import Picrawler
from robot_hat import get_battery_voltage

NEUTRAL = [[60, 0, -30]] * 4     # SunFounder neutral calibration stance
REPOSE_SPEED = 30                # gentle re-pose after each change

# Leg + joint labels for the 12 offsets (leg order matches Picrawler offset list).
LEGS = [
    ("Leg 1 — Front-Right", ["J1", "J2", "J3"]),
    ("Leg 2 — Front-Left",  ["J1", "J2", "J3"]),
    ("Leg 3 — Back-Left",   ["J1", "J2", "J3"]),
    ("Leg 4 — Back-Right",  ["J1", "J2", "J3"]),
]

app = Flask(__name__)
_lock = threading.Lock()
crawler = Picrawler()


def _offsets():
    return [float(x) for x in list(getattr(crawler, "offset", [0.0] * 12))]


def _repose():
    crawler.do_step(NEUTRAL, REPOSE_SPEED)


@app.route("/")
def index():
    labels = []
    for name, joints in LEGS:
        for j in joints:
            labels.append(f"{name} · {j}")
    return render_template("index.html", labels=labels)


@app.route("/api/state")
def state():
    with _lock:
        offs = _offsets()
    try:
        v = round(float(get_battery_voltage()), 2)
    except Exception:
        v = None
    return jsonify(offsets=offs, battery=v)


@app.route("/api/offset", methods=["POST"])
def set_offset():
    body = request.get_json(silent=True) or {}
    try:
        idx = int(body["index"])
        val = float(body["value"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="need index (0-11) and value"), 400
    if not 0 <= idx <= 11:
        return jsonify(error="index must be 0-11"), 400
    val = max(-20.0, min(20.0, val))
    with _lock:
        offs = _offsets()
        offs[idx] = val
        crawler.set_offset(offs)   # persists to ~/.config/.picrawler.config
        _repose()                  # re-apply stance so the leg visibly moves
        offs = _offsets()
    return jsonify(offsets=offs)


@app.route("/api/stance", methods=["POST"])
def stance():
    with _lock:
        _repose()
    return jsonify(ok=True)


@app.route("/api/zero", methods=["POST"])
def zero():
    """Raw-zero every servo to 0 deg (assembly reference), one at a time."""
    from robot_hat import Servo
    import time
    with _lock:
        for i in range(12):
            Servo(i).angle(0)
            time.sleep(0.12)
    return jsonify(ok=True)


@app.route("/api/reset", methods=["POST"])
def reset():
    """Clear all offsets to 0 and re-pose."""
    with _lock:
        crawler.set_offset([0.0] * 12)
        _repose()
        offs = _offsets()
    return jsonify(offsets=offs)


if __name__ == "__main__":
    # bring it to the neutral stance on boot so sliders have a visible baseline
    try:
        _repose()
    except Exception as e:
        print("initial repose failed:", e)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
            threaded=True)
