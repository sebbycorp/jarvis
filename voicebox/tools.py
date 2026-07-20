"""Tools the assistant can call.

Regex intents got the box this far, but they cap out fast: "play some Pink
Floyd" matches, "play something mellow and skip anything long" cannot. Tools
hand the parsing to the model instead.

The trade-off is latency — a tool call costs an extra round trip to the gateway
(~0.7s each way) where a regex is instant. So assistant.py keeps regex
fast-paths for the exact common phrasings and falls through to tools for
everything else.

Each tool declares an OpenAI-style schema and a handler. Handlers return a
dict; whatever they return is fed back to the model as the tool result, so keep
it small and factual.
"""
from __future__ import annotations
import json
from collections.abc import Callable

import config

_REGISTRY: dict[str, dict] = {}


def tool(name: str, description: str, params: dict | None = None,
         required: list[str] | None = None):
    """Register a handler as a callable tool."""
    def wrap(fn: Callable) -> Callable:
        _REGISTRY[name] = {
            "handler": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": params or {},
                        "required": required or [],
                    },
                },
            },
        }
        return fn
    return wrap


def schemas() -> list[dict]:
    return [t["schema"] for t in _REGISTRY.values()]


def names() -> list[str]:
    return sorted(_REGISTRY)


def dispatch(name: str, arguments: str | dict) -> dict:
    """Run a tool call. Never raises — the model gets the error as a result."""
    entry = _REGISTRY.get(name)
    if entry is None:
        return {"error": f"no such tool: {name}"}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"error": f"could not parse arguments: {arguments[:120]}"}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be an object"}
    try:
        return entry["handler"](**arguments)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}


# ==== music ================================================================
@tool("play_music",
      "Play music from the local library. Matches loosely on filename, so an "
      "artist or partial title works.",
      {"query": {"type": "string",
                 "description": "artist or title; omit to play everything"},
       "shuffle": {"type": "boolean", "description": "shuffle the order"}})
def play_music(query: str | None = None, shuffle: bool = False) -> dict:
    import music
    return music.get_player().play(query, shuffle=shuffle)


@tool("stop_music", "Stop music playback.")
def stop_music() -> dict:
    import music
    return music.get_player().stop()


@tool("skip_track", "Skip to the next track.")
def skip_track() -> dict:
    import music
    return music.get_player().skip()


@tool("list_music", "List what is in the local music library.")
def list_music() -> dict:
    import os
    import music
    tracks = [os.path.basename(p) for p in music.get_player().library()]
    return {"count": len(tracks), "tracks": tracks[:40]}


@tool("set_volume", "Set the speaker volume.",
      {"percent": {"type": "integer", "description": "0 to 100"}},
      ["percent"])
def set_volume(percent: int) -> dict:
    import music
    return music.get_player().set_volume(percent)


# ==== vision ===============================================================
@tool("look",
      "Look through the camera and describe what is in front of the box. Use "
      "this whenever the user asks what you can see.")
def look() -> dict:
    # The frame is attached to the follow-up request by the caller rather than
    # returned here — image bytes have no place in a tool result.
    return {"captured": True}


# ==== state ================================================================
@tool("get_status", "Report the box's own state: model backend, music, volume.")
def get_status() -> dict:
    import llm
    import music
    router = llm.get_router()
    return {"backend": router.label(), "music": music.get_player().status(),
            "name": config.WAKE_NAME}


@tool("set_backend",
      "Switch which model answers from now on.",
      {"backend": {"type": "string", "enum": ["local", "openai", "grok"]}},
      ["backend"])
def set_backend(backend: str) -> dict:
    import llm
    if backend not in config.BACKENDS:
        return {"error": f"unknown backend {backend}"}
    router = llm.get_router()
    router.set_default(backend)
    return {"backend": backend, "label": router.label()}
