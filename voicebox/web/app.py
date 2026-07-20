"""Flask control panel for the voice box.

    ~/voicebox-app/.venv/bin/python ~/voicebox-app/web/app.py
    -> http://<pi>:5000

Takes the speaker (and camera, if you use the snapshot) — stop the assistant
service first.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, Response, jsonify, render_template, request  # noqa: E402

import config  # noqa: E402
import llm  # noqa: E402
import music  # noqa: E402
import tts  # noqa: E402

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html", name=config.WAKE_NAME,
                           backends=sorted(config.BACKENDS))


@app.get("/api/status")
def api_status():
    router = llm.get_router()
    return jsonify({
        "name": config.WAKE_NAME,
        "backend": router.default,
        "backend_label": router.label(),
        "tts": "piper" if tts.available() else "espeak",
        "gateway_host": config.GATEWAY_HOST,
        "music": music.get_player().status(),
    })


@app.post("/api/ask")
def api_ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    backend = data.get("backend") or None
    if backend and backend not in config.BACKENDS:
        return jsonify({"error": "unknown backend"}), 400
    try:
        result = llm.get_router().ask(question, backend=backend)
    except llm.LLMError as e:
        return jsonify({"error": str(e)}), 502
    if data.get("speak"):
        tts.say(result["reply"])
    return jsonify(result)


@app.post("/api/speak")
def api_speak():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    return jsonify(tts.say(text[:1000]))


@app.post("/api/backend")
def api_backend():
    data = request.get_json(silent=True) or {}
    backend = data.get("backend")
    if backend not in config.BACKENDS:
        return jsonify({"error": "unknown backend"}), 400
    router = llm.get_router()
    router.set_default(backend)
    return jsonify({"backend": backend, "label": router.label()})


@app.post("/api/reset")
def api_reset():
    llm.get_router().reset()
    return jsonify({"reset": True})


@app.post("/api/music/<action>")
def api_music(action: str):
    data = request.get_json(silent=True) or {}
    player = music.get_player()
    if action == "play":
        return jsonify(player.play(data.get("query") or None,
                                   shuffle=bool(data.get("shuffle"))))
    if action == "stop":
        return jsonify(player.stop())
    if action == "skip":
        return jsonify(player.skip())
    if action == "volume":
        return jsonify(player.set_volume(int(data.get("percent", 70))))
    return jsonify({"error": "unknown action"}), 404


@app.get("/api/library")
def api_library():
    return jsonify({"tracks": [os.path.basename(p)
                               for p in music.get_player().library()]})


@app.get("/snapshot.jpg")
def snapshot():
    import camera
    try:
        return Response(camera.get_camera().capture_jpeg(),
                        mimetype="image/jpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 503


if __name__ == "__main__":
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, threaded=True)
