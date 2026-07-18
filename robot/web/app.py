"""Flask control panel + MJPEG stream for PiCrawler. Serves on :5000.

Run: ~/picrawler-app/.venv/bin/python ~/picrawler-app/web/app.py

NOTE: this process takes exclusive ownership of the Robot HAT and camera.
Do not run it at the same time as the MCP server or AI assistant.
"""
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picrawler-app"))  # find picrawler_ctl
from flask import Flask, render_template, jsonify, Response, request
from picrawler_ctl import get_controller, LowBatteryError

app = Flask(__name__)
c = get_controller()

STEP_ACTIONS = {"forward": c.forward, "backward": c.backward,
                "turn_left": c.turn_left, "turn_right": c.turn_right}
ONESHOT_ACTIONS = {"stand": c.stand, "rest": c.rest, "stop": c.stop}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/move/<action>", methods=["POST"])
def move(action):
    body = request.get_json(silent=True) or {}
    try:
        steps = int(body.get("steps", 1))
    except (TypeError, ValueError):
        steps = 1
    try:
        if action in STEP_ACTIONS:
            return jsonify(STEP_ACTIONS[action](steps))
        if action in ONESHOT_ACTIONS:
            return jsonify(ONESHOT_ACTIONS[action]())
        return jsonify(error="unknown action"), 400
    except LowBatteryError as e:
        return jsonify(error=str(e), low_battery=True), 409


@app.route("/api/pose/<name>", methods=["POST"])
def pose(name):
    try:
        return jsonify(c.pose(name))
    except LowBatteryError as e:
        return jsonify(error=str(e), low_battery=True), 409
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.route("/api/speed", methods=["POST"])
def speed():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(speed=c.set_speed(int(body["speed"])))
    except (KeyError, TypeError, ValueError):
        return jsonify(error="speed must be an integer 1-100"), 400


@app.route("/api/status")
def status():
    return jsonify(c.status())


@app.route("/api/speak", methods=["POST"])
def speak():
    body = request.get_json(silent=True) or {}
    return jsonify(c.speak(str(body.get("text", ""))))


def _mjpeg():
    while True:
        try:
            frame = c.capture_jpeg_bytes()
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                   + frame + b"\r\n")
        except Exception:
            time.sleep(0.2)
        time.sleep(0.05)


@app.route("/stream")
def stream():
    return Response(_mjpeg(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
