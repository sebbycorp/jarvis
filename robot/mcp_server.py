"""FastMCP server exposing PiCrawler control, vision, tuning, and code/deploy.

Transport: streamable-HTTP on MCP_HOST:MCP_PORT at MCP_PATH.
Auth: none unless MCP_AUTH_TOKEN is set (then bearer-token required). LAN default.

Written for FastMCP v3 (auth is passed to the FastMCP() constructor; transport
args go to run()).
"""
from __future__ import annotations
import os
import base64
import subprocess
from pathlib import Path

APP_DIR = Path(os.path.expanduser("~/picrawler-app")).resolve()

# ---- load .env BEFORE importing picrawler_ctl -----------------------------
# picrawler_ctl reads PICRAWLER_MIN/WARN_BATTERY_V from os.environ at import
# time, so the .env must be applied first or those overrides are ignored.
_env = APP_DIR / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from fastmcp import FastMCP  # noqa: E402
from picrawler_ctl import get_controller  # noqa: E402

# ---- build server (auth decided at construction time in v3) ---------------
_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
if _token:
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
    _auth = StaticTokenVerifier(tokens={_token: {"client_id": "lan"}})
    mcp = FastMCP("picrawler", auth=_auth)
else:
    mcp = FastMCP("picrawler")


def _safe_path(rel: str) -> Path:
    """Resolve `rel` under APP_DIR, refusing path escapes."""
    p = (APP_DIR / rel).resolve()
    if p != APP_DIR and APP_DIR not in p.parents:
        raise ValueError(f"path {rel!r} escapes app dir")
    return p


# ==== Runtime control ======================================================
@mcp.tool
def forward(steps: int = 1, speed: int | None = None) -> dict:
    """Walk forward `steps` gait cycles (speed 1-100)."""
    return get_controller().forward(steps, speed)


@mcp.tool
def backward(steps: int = 1, speed: int | None = None) -> dict:
    """Walk backward `steps` gait cycles."""
    return get_controller().backward(steps, speed)


@mcp.tool
def turn_left(steps: int = 1, speed: int | None = None) -> dict:
    """Turn left `steps` gait cycles."""
    return get_controller().turn_left(steps, speed)


@mcp.tool
def turn_right(steps: int = 1, speed: int | None = None) -> dict:
    """Turn right `steps` gait cycles."""
    return get_controller().turn_right(steps, speed)


@mcp.tool
def stand() -> dict:
    """Stand in the neutral pose."""
    return get_controller().stand()


@mcp.tool
def rest() -> dict:
    """Sit/rest pose."""
    return get_controller().rest()


@mcp.tool
def stop() -> dict:
    """Stop and hold a safe standing pose."""
    return get_controller().stop()


@mcp.tool
def pose(name: str) -> dict:
    """Run an expressive pose: wave, push_up, dance, look_up, look_down,
    look_left, look_right, ready."""
    return get_controller().pose(name)


@mcp.tool
def status() -> dict:
    """Return speed, camera/speaker state, and battery voltage/health."""
    return get_controller().status()


@mcp.tool
def battery() -> dict:
    """Read the battery pack voltage (volts) and whether it's safe to move."""
    c = get_controller()
    v = c.battery_voltage()
    return {"battery_v": v, "ok_to_move": v >= __import__("picrawler_ctl").MIN_BATTERY_V}


@mcp.tool
def speak(text: str) -> dict:
    """Speak `text` aloud via the onboard speaker (Espeak TTS)."""
    return get_controller().speak(text)


# ==== Vision ===============================================================
@mcp.tool
def capture_image():
    """Capture a camera frame and return it as an image the client can view."""
    jpg = get_controller().capture_jpeg_bytes()
    try:
        from fastmcp.utilities.types import Image
        return Image(data=jpg, format="jpeg")
    except Exception:
        # Fallback: structured base64 if the Image helper is unavailable
        return {"mime": "image/jpeg", "base64": base64.b64encode(jpg).decode()}


# ==== Tuning ===============================================================
@mcp.tool
def set_speed(speed: int) -> dict:
    """Set default gait speed (1-100)."""
    return {"speed": get_controller().set_speed(speed)}


@mcp.tool
def calibrate_leg(index: int, offset: float) -> dict:
    """Set one servo trim offset (index 0-11, clamped +/-20 degrees) and persist."""
    return get_controller().set_leg_offset(index, offset)


@mcp.tool
def get_offsets() -> dict:
    """Return the 12 persisted servo trim offsets."""
    return {"offsets": get_controller().get_offsets()}


@mcp.tool
def set_battery_guard(enabled: bool) -> dict:
    """Enable/disable the pre-move low-battery guard (default enabled)."""
    return get_controller().set_battery_guard(enabled)


# ==== Code + deploy (scoped to ~/picrawler-app) ============================
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
def restart_robot_service(service: str = "picrawler-mcp") -> dict:
    """Restart a systemd service (picrawler-mcp or picrawler-web)."""
    if service not in {"picrawler-mcp", "picrawler-web"}:
        raise ValueError("service must be picrawler-mcp or picrawler-web")
    out = subprocess.run(["sudo", "systemctl", "restart", service],
                         capture_output=True, text=True)
    return {"service": service, "rc": out.returncode,
            "stderr": out.stderr.strip()}


if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    path = os.environ.get("MCP_PATH", "/mcp")
    mcp.run(transport="http", host=host, port=port, path=path)
