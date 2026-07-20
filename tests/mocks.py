"""Stubs so the voice box imports off-device.

The Pi has sounddevice, numpy, picamera2 and friends; a laptop running the unit
tests usually doesn't. These stubs exist only to satisfy *import* — any test
that would exercise real DSP belongs on the hardware, not here.
"""
from __future__ import annotations
import os
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "voicebox"))

# Point the app dir somewhere harmless so config never reads a real ~/.env
os.environ.setdefault("VOICEBOX_APP_DIR", str(REPO / "tests" / ".appdir"))


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return mod


def install() -> None:
    """Install stubs for modules that only exist on the Pi."""
    if "numpy" not in sys.modules:
        try:
            import numpy  # noqa: F401
        except ImportError:
            _stub("numpy", int16="int16", float32="float32",
                  frombuffer=lambda *a, **k: [], concatenate=lambda *a, **k: [],
                  empty=lambda *a, **k: [], sqrt=lambda x: x,
                  mean=lambda x: 0.0)

    if "sounddevice" not in sys.modules:
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            class _Stream:
                def __init__(self, *a, **k): pass
                def start(self): pass
                def stop(self): pass
                def close(self): pass

            _stub("sounddevice", RawInputStream=_Stream,
                  query_devices=lambda *a, **k: [],
                  check_input_settings=lambda *a, **k: None)


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def chat_response(content: str | None = None, reasoning: str | None = None) -> dict:
    """Build an OpenAI-shaped chat/completions body."""
    message: dict = {"role": "assistant"}
    if content is not None:
        message["content"] = content
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {"choices": [{"message": message, "finish_reason": "stop"}]}
