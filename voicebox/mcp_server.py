"""FastMCP server exposing the voice box to remote agents.

Transport: streamable-HTTP on MCP_HOST:MCP_PORT at MCP_PATH.
Auth: none unless MCP_AUTH_TOKEN is set (then bearer-token required). LAN default.

Note the MCP server and the assistant loop both want the microphone. Run one at
a time (`make stop-all`), or leave MCP as the always-on service and use `listen`
for on-demand capture.
"""
from __future__ import annotations
import base64
import subprocess
from pathlib import Path

import config  # loads .env before anything reads os.environ

from fastmcp import FastMCP  # noqa: E402

import llm  # noqa: E402
import music  # noqa: E402
import tts  # noqa: E402

APP_DIR = config.APP_DIR
SERVICES = {"voicebox", "voicebox-mcp", "voicebox-web"}

if config.MCP_AUTH_TOKEN:
    StaticTokenVerifier = None
    for _modpath in ("fastmcp.server.auth",
                     "fastmcp.server.auth.providers.bearer",
                     "fastmcp.server.auth.providers.jwt"):
        try:
            _mod = __import__(_modpath, fromlist=["StaticTokenVerifier"])
            StaticTokenVerifier = getattr(_mod, "StaticTokenVerifier")
            break
        except (ImportError, AttributeError):
            continue
    if StaticTokenVerifier is None:
        raise ImportError("could not locate StaticTokenVerifier in fastmcp; "
                          "check the installed version's auth module layout")
    mcp = FastMCP("voicebox", auth=StaticTokenVerifier(
        tokens={config.MCP_AUTH_TOKEN: {"client_id": "lan"}}))
else:
    mcp = FastMCP("voicebox")


def _safe_path(rel: str) -> Path:
    """Resolve `rel` under APP_DIR, refusing path escapes."""
    p = (APP_DIR / rel).resolve()
    if p != APP_DIR and APP_DIR not in p.parents:
        raise ValueError(f"path {rel!r} escapes app dir")
    return p


# ==== Voice ================================================================
@mcp.tool
def speak(text: str) -> dict:
    """Speak `text` aloud through the box's speaker (local Piper TTS)."""
    return tts.say(text)


@mcp.tool
def listen(seconds: float = 8.0) -> dict:
    """Record from the microphone until the speaker stops (or `seconds`
    elapse) and return the local transcription."""
    import audio
    import stt
    import wake
    seconds = max(1.0, min(30.0, float(seconds)))
    recorder = wake.Recorder()
    original, config.MAX_UTTERANCE_S = config.MAX_UTTERANCE_S, seconds
    try:
        with audio.Microphone() as mic:
            pcm = recorder.record(mic.frames())
    finally:
        config.MAX_UTTERANCE_S = original
    if not pcm:
        return {"text": "", "heard": False}
    return {"text": stt.get_transcriber().transcribe_pcm(pcm), "heard": True}


@mcp.tool
def ask(question: str, backend: str | None = None,
        speak_reply: bool = False) -> dict:
    """Ask a model through the gateway. `backend` is local, openai, or grok
    (default: the box's current backend). Optionally speak the answer."""
    if backend and backend not in config.BACKENDS:
        raise ValueError(f"backend must be one of {sorted(config.BACKENDS)}")
    result = llm.get_router().ask(question, backend=backend)
    if speak_reply:
        tts.say(result["reply"])
    return result


@mcp.tool
def set_backend(backend: str) -> dict:
    """Set the default model backend: local (Qwen), openai, or grok."""
    if backend not in config.BACKENDS:
        raise ValueError(f"backend must be one of {sorted(config.BACKENDS)}")
    router = llm.get_router()
    router.set_default(backend)
    return {"backend": backend, "label": router.label()}


@mcp.tool
def reset_conversation() -> dict:
    """Clear the assistant's rolling conversation history."""
    llm.get_router().reset()
    return {"reset": True}


# ==== Vision ===============================================================
@mcp.tool
def capture_image():
    """Capture a camera frame and return it as an image the client can view."""
    import camera
    jpg = camera.get_camera().capture_jpeg()
    try:
        from fastmcp.utilities.types import Image
        return Image(data=jpg, format="jpeg")
    except Exception:
        return {"mime": "image/jpeg", "base64": base64.b64encode(jpg).decode()}


@mcp.tool
def take_photo(name: str | None = None) -> dict:
    """Capture a frame and save it to the photo directory."""
    import camera
    return {"path": camera.get_camera().save_photo(name)}


# ==== Music ================================================================
@mcp.tool
def play_music(query: str | None = None, shuffle: bool = False) -> dict:
    """Play from the local music library; `query` matches on filename."""
    return music.get_player().play(query, shuffle=shuffle)


@mcp.tool
def stop_music() -> dict:
    """Stop playback and clear the queue."""
    return music.get_player().stop()


@mcp.tool
def skip_track() -> dict:
    """Skip to the next queued track."""
    return music.get_player().skip()


@mcp.tool
def set_volume(percent: int) -> dict:
    """Set the output volume (0-100)."""
    return music.get_player().set_volume(percent)


# ==== Status ===============================================================
@mcp.tool
def status() -> dict:
    """Report backends, audio engines, music state, and camera state."""
    import camera
    router = llm.get_router()
    st = {
        "name": config.WAKE_NAME,
        "backend": router.default,
        "backend_label": router.label(),
        "backends": sorted(config.BACKENDS),
        "gateway_host": config.GATEWAY_HOST,
        "tts": "piper" if tts.available() else "espeak",
        "wake_word": config.WAKE_MODEL if config.WAKE_ENABLED else None,
        "camera_started": camera.get_camera().started,
        "music": music.get_player().status(),
    }
    try:
        from robot_hat import ADC
        st["battery_v"] = round(ADC("A4").read() / 4095.0 * 3.3 * 3, 2)
    except Exception:
        st["battery_v"] = None  # HAT absent or running on wall power
    return st


# ==== Code + deploy (scoped to the app dir) ================================
@mcp.tool
def list_files(subdir: str = ".") -> dict:
    """List files under the app dir."""
    base = _safe_path(subdir)
    return {"path": str(base),
            "entries": sorted(p.name + ("/" if p.is_dir() else "")
                              for p in base.iterdir())}


@mcp.tool
def read_file(path: str) -> dict:
    """Read a text file under the app dir."""
    return {"path": path, "content": _safe_path(path).read_text()}


@mcp.tool
def write_file(path: str, content: str) -> dict:
    """Write a text file under the app dir (creates parent dirs)."""
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"path": path, "bytes": len(content)}


@mcp.tool
def restart_service(service: str = "voicebox") -> dict:
    """Restart a systemd service (voicebox, voicebox-mcp, or voicebox-web)."""
    if service not in SERVICES:
        raise ValueError(f"service must be one of {sorted(SERVICES)}")
    out = subprocess.run(["sudo", "systemctl", "restart", service],
                         capture_output=True, text=True)
    return {"service": service, "rc": out.returncode,
            "stderr": out.stderr.strip()}


if __name__ == "__main__":
    mcp.run(transport="http", host=config.MCP_HOST, port=config.MCP_PORT,
            path=config.MCP_PATH)
