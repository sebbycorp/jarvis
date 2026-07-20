"""MCP client — lets the assistant reach tools that live off the box.

The box also *serves* MCP (mcp_server.py) so cluster agents can drive it. This
is the other direction: Jarvis calling out to MCP servers on the LAN, which is
what turns "answers questions about the world" into "does things in it".

Talks streamable-HTTP JSON-RPC directly with `requests` rather than pulling in
an MCP SDK: the box already depends on requests, the protocol surface needed
here is three calls (initialize, tools/list, tools/call), and a synchronous
client suits the voice loop, which is synchronous anyway.

Tool names are namespaced (`weather.getWeatherForecast`) so two servers can
both expose `search` without colliding, and so the model's choice says which
server it meant.
"""
from __future__ import annotations
import json
import threading
import time

import requests

import config

_HEADERS = {"content-type": "application/json",
            "accept": "application/json, text/event-stream"}


def _parse(text: str) -> dict:
    """Read a JSON-RPC reply out of either a plain body or an SSE stream."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


class MCPServer:
    """One remote MCP server: session handling, tool listing, tool calls."""

    def __init__(self, name: str, url: str, allow: list[str] | None = None):
        self.name = name
        self.url = url
        self.allow = allow or []
        self.session: str | None = None
        self.tools: list[dict] = []
        self.error: str | None = None
        self._lock = threading.Lock()

    # ---- session -----------------------------------------------------------
    def _open(self) -> bool:
        """Initialize a session. Sessions expire, so this is re-callable."""
        try:
            r = requests.post(self.url, headers=_HEADERS, timeout=config.MCP_TIMEOUT,
                              json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18",
                                               "capabilities": {},
                                               "clientInfo": {"name": config.WAKE_NAME,
                                                              "version": "1"}}})
        except requests.RequestException as e:
            self.error = f"unreachable: {e}"
            return False
        if r.status_code != 200:
            self.error = f"HTTP {r.status_code}"
            return False
        self.session = r.headers.get("mcp-session-id")
        if not self.session:
            self.error = "no session id returned"
            return False
        self._notify()
        self.error = None
        return True

    def _headers(self) -> dict:
        h = dict(_HEADERS)
        if self.session:
            h["mcp-session-id"] = self.session
        return h

    def _notify(self) -> None:
        try:
            requests.post(self.url, headers=self._headers(),
                          timeout=config.MCP_TIMEOUT,
                          json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        except requests.RequestException:
            pass

    def _rpc(self, method: str, params: dict | None = None,
             retry: bool = True) -> dict:
        if self.session is None and not self._open():
            return {"error": {"message": self.error or "no session"}}
        body = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 100000,
                "method": method}
        if params is not None:
            body["params"] = params
        try:
            r = requests.post(self.url, headers=self._headers(),
                              timeout=config.MCP_TIMEOUT, json=body)
        except requests.RequestException as e:
            return {"error": {"message": f"{self.name} unreachable: {e}"}}
        # A dropped/expired session shows up as 4xx — re-initialize once.
        if r.status_code in (400, 401, 404) and retry:
            self.session = None
            return self._rpc(method, params, retry=False)
        if r.status_code != 200:
            return {"error": {"message": f"{self.name} HTTP {r.status_code}"}}
        return _parse(r.text)

    # ---- tools -------------------------------------------------------------
    def refresh(self) -> list[dict]:
        """Fetch the tool list, applying the allowlist."""
        with self._lock:
            reply = self._rpc("tools/list")
            if "error" in reply:
                self.error = str(reply["error"].get("message", reply["error"]))
                self.tools = []
                return []
            tools = reply.get("result", {}).get("tools", [])
            if self.allow:
                wanted = {a.lower() for a in self.allow}
                tools = [t for t in tools if t.get("name", "").lower() in wanted]
            self.tools = tools
            self.error = None
            return tools

    def schemas(self) -> list[dict]:
        """OpenAI-style function schemas, namespaced by server."""
        out = []
        for t in self.tools:
            params = t.get("inputSchema") or {"type": "object", "properties": {}}
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            out.append({"type": "function", "function": {
                "name": f"{self.name}{config.MCP_NAME_SEP}{t['name']}",
                "description": (t.get("description")
                                or f"{t['name']} on {self.name}")[:1024],
                "parameters": params}})
        return out

    def call(self, tool: str, arguments: dict) -> dict:
        with self._lock:
            reply = self._rpc("tools/call",
                              {"name": tool, "arguments": arguments or {}})
        if "error" in reply:
            return {"error": str(reply["error"].get("message", reply["error"]))}
        result = reply.get("result", {})
        # Flatten MCP content blocks into something a model can read aloud.
        parts = []
        for block in result.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(f"[{block.get('type', 'content')}]")
        text = "\n".join(p for p in parts if p).strip()
        if result.get("structuredContent"):
            return {"result": result["structuredContent"], "text": text[:1500]}
        return {"result": text[:1500] or "ok",
                "is_error": bool(result.get("isError"))}


class MCPRegistry:
    """All configured remote servers."""

    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def configure(self) -> None:
        for name, url, allow in config.mcp_servers():
            self.servers[name] = MCPServer(name, url, allow)

    def load(self, force: bool = False) -> dict[str, int]:
        """Connect and list tools. Failures are recorded, never raised — an
        unreachable MCP server must not stop the box from answering."""
        with self._lock:
            if self._loaded and not force:
                return {n: len(s.tools) for n, s in self.servers.items()}
            if not self.servers:
                self.configure()
            counts = {}
            for name, server in self.servers.items():
                try:
                    counts[name] = len(server.refresh())
                except Exception as e:
                    server.error = str(e)
                    counts[name] = 0
            self._loaded = True
            return counts

    def schemas(self) -> list[dict]:
        out: list[dict] = []
        for server in self.servers.values():
            out.extend(server.schemas())
        return out[:config.MCP_TOOL_LIMIT]

    def dispatch(self, qualified: str, arguments: dict) -> dict:
        if config.MCP_NAME_SEP not in qualified:
            return {"error": f"not a remote tool: {qualified}"}
        name, _, tool = qualified.partition(config.MCP_NAME_SEP)
        server = self.servers.get(name)
        if server is None:
            return {"error": f"unknown MCP server: {name}"}
        return server.call(tool, arguments)

    def handles(self, qualified: str) -> bool:
        name = qualified.partition(config.MCP_NAME_SEP)[0]
        return config.MCP_NAME_SEP in qualified and name in self.servers

    def status(self) -> dict:
        return {n: {"url": s.url, "tools": len(s.tools), "error": s.error}
                for n, s in self.servers.items()}


_registry: MCPRegistry | None = None
_lock = threading.Lock()


def get_registry() -> MCPRegistry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = MCPRegistry()
            _registry.configure()
    return _registry
