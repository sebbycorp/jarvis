"""Model router: one OpenAI-compatible client over three AgentGateway routes.

Backends (see config.BACKENDS):
  local   -> DGX Spark / vLLM, Qwen           (default: fast, private, offline-ish)
  openai  -> gateway's pinned OpenAI model
  grok    -> xAI Grok

The gateway proxies only /chat/completions — no audio, embeddings, or responses
endpoints — so speech stays local (see stt.py / tts.py).

A spoken turn can be routed per-utterance: "ask Grok what the weather is" runs
that one turn on Grok without changing the default. "Switch to Grok" changes the
default until told otherwise.
"""
from __future__ import annotations
import base64
import json
import re
import threading

import requests

import config

# Spoken aliases -> backend name. Longest match wins, so order doesn't matter.
# These are matched against *whisper output*, not what the user meant, so the
# misspellings matter: whisper has produced "Groc", "Grock" and even "rock"
# (the leading G clipped) for Grok. Missing a variant silently routes the turn
# to the wrong model. Ambiguous entries like "rock" are safe because every
# routing pattern requires an ask-verb, a greeting, or punctuation before the
# name — "play some rock music" is a question, not a route.
ALIASES: dict[str, str] = {
    "local": "local", "qwen": "local", "quen": "local", "quinn": "local",
    "gwen": "local", "spark": "local", "offline": "local",
    "gpt": "openai", "chat gpt": "openai", "chatgpt": "openai",
    "open ai": "openai", "openai": "openai", "g p t": "openai",
    "grok": "grok", "grock": "grok", "groc": "grok", "grok's": "grok",
    "crock": "grok", "rock": "grok", "brock": "grok", "grog": "grok",
    "x a i": "grok", "xai": "grok",
}
_ALIAS_RE = "|".join(sorted((re.escape(a) for a in ALIASES), key=len, reverse=True))

# The recorder starts mid-wake-word, so whisper prepends junk: "hey jarvis, ask
# grok ..." comes through as "This asks Grock, ...". Routing therefore cannot be
# anchored to the start of the utterance — it looks for an ask-verb followed by
# a backend name anywhere in the sentence, and treats the remainder as the
# prompt. Verb forms are loose ("asks", "asked") for the same reason.
_ASK_VERBS = r"ask|asks|asked|use|uses|using|try|tries|query|queries|tell|tells"
_ONE_SHOT_PATTERNS = (
    # "…ask grok why the sky is blue"  (the common case, wake-residue tolerant)
    re.compile(rf"\b(?:{_ASK_VERBS})\s+({_ALIAS_RE})\b[,:]?\s*(.*)$", re.I | re.S),
    # "hey grok why is the sky blue" — a greeting makes the name unambiguous
    re.compile(rf"^\s*(?:hey|ok|okay)\s+({_ALIAS_RE})\b[,:]?\s+(.+)$", re.I | re.S),
    # "grok: what time is it" — a bare name needs punctuation, or "local
    # weather today" would route instead of being asked as a question
    re.compile(rf"^\s*({_ALIAS_RE})\b\s*[,:]\s*(.+)$", re.I | re.S),
)
# "switch to grok", "use grok from now on" -> change the default
_SWITCH_RE = re.compile(
    rf"^\s*(?:please\s+)?(?:switch|change)\s+(?:to|over to)\s+({_ALIAS_RE})\b"
    rf"|^\s*use\s+({_ALIAS_RE})\s+(?:from now on|for everything|by default)\b",
    re.I)

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S | re.I)

# Recording begins part-way through the wake word, so whisper transcribes a
# fragment of it: "hey jarvis" has come through as "This", "Hey Jarvis",
# "Jarvis" and "Charvis". Strip a leading greeting and/or wake-word-shaped
# token so it never lands in the prompt sent to a model.
_WAKE_RESIDUE_RE = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay|yo|this|that|the)\b[\s,.:!-]*)?"
    r"(?:(?:jarvis|jarvi[sz]|charvis|travis|garvis|service|jervis)\b[\s,.:!-]*)?",
    re.I)


class LLMError(RuntimeError):
    pass


def normalize(text: str) -> str:
    """Flatten a model reply into something worth speaking aloud."""
    text = _THINK_RE.sub("", text or "").strip()
    text = re.sub(r"```[^\n]*\n?|`", "", text)          # code fences / ticks
    text = re.sub(r"^\s*[#>*\-+]\s+", "", text, flags=re.M)  # md bullets
    text = re.sub(r"\*\*|__", "", text)                  # bold/italic markers
    return re.sub(r"\n{2,}", "\n", text).strip()


def strip_wake_residue(text: str) -> str:
    """Drop the wake word (and whatever whisper made of its clipped start) from
    the front of an utterance."""
    return _WAKE_RESIDUE_RE.sub("", text, count=1).lstrip(" ,.:;-").strip()


def parse_route(text: str) -> tuple[str | None, str, bool]:
    """Return (backend, remaining_text, is_permanent_switch).

    backend is None when the utterance names no backend.
    """
    # Try the utterance as-is before stripping: "hey gpt tell me a joke" needs
    # the greeting that residue-stripping would remove. Then try it stripped,
    # for the "This asks Grock…" shape where junk precedes the real request.
    stripped = strip_wake_residue(text)
    for candidate in dict.fromkeys((text.strip(), stripped)):
        if not candidate:
            continue
        m = _SWITCH_RE.search(candidate)
        if m:
            alias = (m.group(1) or m.group(2) or "").lower()
            return ALIASES[alias], "", True
        for pattern in _ONE_SHOT_PATTERNS:
            m = pattern.search(candidate)
            if m:
                alias, rest = m.group(1).lower(), m.group(2).strip()
                if rest:  # a backend name with no question is not a request
                    return ALIASES[alias], rest, False
    return None, stripped or text.strip(), False


class Router:
    """Holds the default backend and the rolling conversation history."""

    def __init__(self, backend: str | None = None):
        backend = backend or config.DEFAULT_BACKEND
        if backend not in config.BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        self.default = backend
        self._history: list[dict] = []
        self._lock = threading.Lock()

    # ---- state -------------------------------------------------------------
    def set_default(self, backend: str) -> str:
        if backend not in config.BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        with self._lock:
            self.default = backend
        return backend

    def reset(self) -> None:
        with self._lock:
            self._history.clear()

    def label(self, backend: str | None = None) -> str:
        return config.BACKENDS[backend or self.default]["label"]

    # ---- the one call that matters ----------------------------------------
    def ask(self, text: str, image_jpeg: bytes | None = None,
            backend: str | None = None, remember: bool = True,
            use_tools: bool | None = None) -> dict:
        """Send `text` (+ optional camera frame) and return the spoken reply."""
        route, stripped, permanent = parse_route(text)
        if permanent and route:
            self.set_default(route)
            return {"backend": route, "text": stripped,
                    "reply": f"Okay, using {self.label(route)} from now on.",
                    "switched": True}

        name = backend or route or self.default
        cfg = config.BACKENDS[name]
        prompt = stripped if route else text

        with self._lock:
            history = list(self._history)

        content: object = prompt
        if image_jpeg and cfg["vision"]:
            b64 = base64.b64encode(image_jpeg).decode()
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]

        messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
        messages += history
        messages.append({"role": "user", "content": content})

        if use_tools is None:
            use_tools = config.TOOLS_ENABLED
        tool_schemas = None
        if use_tools:
            import tools
            tool_schemas = tools.schemas()

        reply, called = self._converse(name, cfg, messages, tool_schemas)

        if remember:
            with self._lock:
                # store the plain text prompt, never the image, to keep the
                # rolling context small enough for a Pi-side voice loop
                self._history.append({"role": "user", "content": prompt})
                self._history.append({"role": "assistant", "content": reply})
                keep = config.HISTORY_TURNS * 2
                if len(self._history) > keep:
                    del self._history[:-keep]

        return {"backend": name, "text": prompt, "reply": reply,
                "switched": False, "tools_used": called,
                "saw_image": bool(image_jpeg and cfg["vision"])}

    def _converse(self, name: str, cfg: dict, messages: list,
                  tool_schemas: list | None) -> tuple[str, list[str]]:
        """Exchange with the model, running any tools it asks for.

        Bounded by MAX_TOOL_ROUNDS: a model that keeps calling tools without
        producing an answer would otherwise loop until the user gives up.
        """
        called: list[str] = []
        url = config.backend_url(name)
        for _ in range(max(1, config.MAX_TOOL_ROUNDS)):
            payload = {"messages": messages, "max_tokens": config.MAX_TOKENS,
                       # empty model = let the gateway substitute its pinned one
                       "model": cfg["model"]}
            payload.update(cfg.get("extra") or {})
            if tool_schemas:
                payload["tools"] = tool_schemas

            msg = self._post(url, payload, name)
            calls = msg.get("tool_calls") or []
            if not calls:
                return self._content(msg, name), called

            import tools
            messages.append({k: v for k, v in msg.items()
                             if k in ("role", "content", "tool_calls")})
            for call in calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                called.append(tool_name)
                result = tools.dispatch(tool_name, fn.get("arguments", "{}"))
                messages.append({"role": "tool",
                                 "tool_call_id": call.get("id", tool_name),
                                 "name": tool_name,
                                 "content": json.dumps(result)[:2000]})
            # drop the tools on the last round so it has to answer in words
            tool_schemas = tool_schemas if len(called) < 6 else None

        # ran out of rounds — report what was done rather than nothing
        if called:
            return f"Done: {', '.join(dict.fromkeys(called))}.", called
        raise LLMError(f"{name} kept calling tools without answering")

    @staticmethod
    def _content(msg: dict, name: str) -> str:
        # Reasoning models (Qwen on vLLM, Grok) split thinking from the answer
        # and may leave `content` empty when the answer got truncated.
        out = normalize(msg.get("content") or "")
        if not out:
            out = normalize(msg.get("reasoning_content")
                            or msg.get("reasoning") or "")
        if not out:
            raise LLMError(f"{name} returned an empty reply")
        return out

    def _post(self, url: str, payload: dict, name: str) -> dict:
        try:
            r = requests.post(url, json=payload,
                              headers={"content-type": "application/json"},
                              timeout=config.LLM_TIMEOUT)
        except requests.RequestException as e:
            raise LLMError(f"{name} gateway unreachable: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"{name} gateway returned {r.status_code}: "
                           f"{r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError(f"{name} sent an unexpected response: "
                           f"{r.text[:200]}") from e


_router: Router | None = None
_router_lock = threading.Lock()


def get_router() -> Router:
    global _router
    with _router_lock:
        if _router is None:
            _router = Router()
    return _router
