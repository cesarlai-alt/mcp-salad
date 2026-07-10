#!/usr/bin/env python3
"""
Vero MCP Router — Phase 1
Dynamic schema injection proxy for local MCP servers.

Architecture:
  Claude Code <--stdio--> router.py <--subprocess/http--> child MCP servers

Exposes 3 meta-tools to Claude:
  - list_capabilities()         : show available capability groups
  - use_capability(description) : load a capability's tools into context
  - drop_capability(name)       : unload a capability (free context)

Child MCP tools are then proxied directly so Claude can call them.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# MCP SDK imports
from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp import types

# ─── Logging setup ─────────────────────────────────────────────────────────────
log_path = Path.home() / ".vero" / "logs" / "mcp-router.log"
log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        # stderr only — stdout is reserved for MCP stdio protocol
    ],
)
log = logging.getLogger("vero-router")

# ─── Config loading ─────────────────────────────────────────────────────────────
# Config path defaults to gateway/config.yaml next to this file, but can be
# overridden via the MCP_ROUTER_CONFIG env var (used by tests and alternate
# deployments). Kept backward-compatible: unset → identical to previous behavior.
CONFIG_PATH = Path(
    os.environ.get("MCP_ROUTER_CONFIG", str(Path(__file__).parent / "config.yaml"))
)


def load_config() -> dict:
    # Resilient load: a missing or empty config must NOT crash module import
    # (the control-channel CLI, tests, and `import router` smoke checks all rely
    # on the module importing cleanly even when no config file is present).
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning(f"Config not found at {CONFIG_PATH}; starting with empty config.")
        return {}


CONFIG = load_config()
CAPABILITIES: dict = CONFIG.get("capabilities", {})
SERVER_DEFS: dict = CONFIG.get("servers", {})

# ─── State ─────────────────────────────────────────────────────────────────────
# Maps capability_name -> list of tool dicts fetched from child servers
active_capabilities: dict[str, list[dict]] = {}

# Maps server_id -> running subprocess (for stdio servers)
child_processes: dict[str, asyncio.subprocess.Process] = {}

# Cache of tool lists per server_id
server_tool_cache: dict[str, list[dict]] = {}

# ─── Out-of-band control channel state ───────────────────────────────────────────
# A live reference to the MCP ServerSession, captured on the first real request.
#
# WHY THIS EXISTS: notifications/tools/list_changed is normally sent via
# `app.request_context.session`, but `request_context` is a contextvar that is
# ONLY set while an inbound tool call is being handled. The out-of-band control
# socket task (below) runs OUTSIDE any request context, so it cannot read that
# contextvar. Instead we stash the ServerSession object the first time a real
# request arrives (see list_tools/call_tool handlers) and call
# send_tool_list_changed() on it directly from the background task. The MCP
# client issues tools/list during initialization, so this is populated within
# milliseconds of connect — well before any manual `enable`/`disable`.
_active_session = None

# Unix-domain control socket path. Override with MCP_SALAD_CONTROL_SOCK (tests
# point this at a temp path). Cross-platform note: AF_UNIX sockets are macOS/Linux
# only; a Windows port would need a different transport (named pipe or TCP loopback).
CONTROL_SOCK_PATH = Path(
    os.environ.get(
        "MCP_SALAD_CONTROL_SOCK",
        str(Path.home() / ".mcp-salad" / "gateway.sock"),
    )
)

# ─── Keyword matching ──────────────────────────────────────────────────────────

def match_capability(description: str) -> str | None:
    """
    Two-stage match:
    1. Keyword scan (fast, case-insensitive)
    2. Returns None if no match (Claude falls back to list_capabilities)
    """
    desc_lower = description.lower()

    best_cap = None
    best_score = 0

    for cap_name, cap_def in CAPABILITIES.items():
        keywords: list[str] = cap_def.get("keywords", [])
        score = sum(1 for kw in keywords if str(kw).lower() in desc_lower)
        if score > best_score:
            best_score = score
            best_cap = cap_name

    if best_score > 0:
        log.info(f"Matched capability '{best_cap}' (score={best_score}) for: {description[:60]}")
        return best_cap

    log.info(f"No keyword match for: {description[:60]}")
    return None


def find_capability_by_server(server_id: str) -> str | None:
    """Find which capability owns a given server_id."""
    for cap_name, cap_def in CAPABILITIES.items():
        if server_id in cap_def.get("servers", []):
            return cap_name
    return None


# ─── Child MCP communication ────────────────────────────────────────────────────

class StdioMCPClient:
    """Minimal MCP stdio client — sends JSON-RPC over subprocess stdin/stdout."""

    def __init__(self, server_id: str, server_def: dict):
        self.server_id = server_id
        self.server_def = server_def
        self.proc: asyncio.subprocess.Process | None = None
        self._msg_id = 0
        self._initialized = False

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _ensure_started(self):
        if self.proc and self.proc.returncode is None:
            return  # already running

        cmd = self.server_def["command"]
        args = self.server_def.get("args", [])
        env_overrides = self.server_def.get("env", {})

        env = os.environ.copy()
        for k, v in env_overrides.items():
            # Expand ${VAR} references from real environment
            if v.startswith("${") and v.endswith("}"):
                env_key = v[2:-1]
                env[k] = os.environ.get(env_key, "")
            else:
                env[k] = v

        log.info(f"Starting child MCP: {cmd} {' '.join(str(a) for a in args)}")
        self.proc = await asyncio.create_subprocess_exec(
            cmd, *[str(a) for a in args],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        child_processes[self.server_id] = self.proc
        await self._initialize()

    async def _send(self, method: str, params: dict | None = None) -> Any:
        await self._ensure_started()

        msg_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        raw = json.dumps(request) + "\n"
        self.proc.stdin.write(raw.encode())
        await self.proc.stdin.drain()

        # Read responses until we find the one matching our msg_id.
        # Child servers may send unsolicited notifications (e.g.
        # notifications/tools/list_changed) between request and response;
        # skip those instead of misinterpreting them as our reply.
        deadline = asyncio.get_event_loop().time() + 30.0
        buf = b""
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.error(f"Timeout waiting for response from {self.server_id} (method={method})")
                raise asyncio.TimeoutError(
                    f"Timeout waiting for id={msg_id} from {self.server_id}"
                )
            try:
                chunk = await asyncio.wait_for(
                    self.proc.stdout.read(65536), timeout=remaining
                )
            except asyncio.TimeoutError:
                log.error(f"Timeout waiting for response from {self.server_id} (method={method})")
                raise

            if not chunk:
                raise RuntimeError(f"Child MCP {self.server_id} closed stdout unexpectedly")

            buf += chunk
            # Process every complete newline-terminated JSON object in the buffer
            while b"\n" in buf:
                nl = buf.index(b"\n")
                line = buf[:nl].strip()
                buf = buf[nl + 1:]
                if not line:
                    continue
                try:
                    response = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                resp_id = response.get("id")
                if resp_id is None:
                    # Notification from child server — ignore, keep waiting
                    log.debug(
                        f"{self.server_id}: skipping notification "
                        f"method={response.get('method')} while waiting for id={msg_id}"
                    )
                    continue
                if resp_id != msg_id:
                    # Response to a different request — should not happen in
                    # single-threaded sequential calls, but be safe
                    log.warning(
                        f"{self.server_id}: got response for id={resp_id} "
                        f"while waiting for id={msg_id}, discarding"
                    )
                    continue
                # Matched our request
                if "error" in response:
                    raise RuntimeError(f"MCP error from {self.server_id}: {response['error']}")
                return response.get("result")

    async def _notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        notification: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            notification["params"] = params
        raw = json.dumps(notification) + "\n"
        self.proc.stdin.write(raw.encode())
        await self.proc.stdin.drain()

    async def _initialize(self):
        """Send MCP initialize handshake."""
        await self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "vero-router", "version": "1.0"},
        })
        # notifications/initialized must be sent as a *notification* (no id).
        # Sending it as a request (with id) causes servers like context7 to
        # return -32601 Method Not Found, breaking subsequent tool listing.
        await self._notify("notifications/initialized")
        self._initialized = True
        log.info(f"Child MCP {self.server_id} initialized")

    async def list_tools(self) -> list[dict]:
        result = await self._send("tools/list")
        tools = result.get("tools", []) if result else []
        log.info(f"  {self.server_id}: fetched {len(tools)} tools")
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        result = await self._send("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return result


class StreamableHttpMCPClient:
    """MCP Streamable HTTP client for remote MCP servers using the MCP 2025-03-26 transport.

    fastmcp-hosted servers (e.g. twstock) use Streamable HTTP (POST-based),
    not the legacy SSE transport.  The MCP Python SDK's streamablehttp_client
    handles this automatically.

    A fresh connection is opened for every operation (correctness over performance).
    """

    def __init__(self, server_id: str, server_def: dict):
        self.server_id = server_id
        self.url = server_def["url"]

    @staticmethod
    def _no_verify_factory(
        headers: dict | None = None,
        timeout: Any = None,
        auth: Any = None,
    ):
        """httpx client factory that disables TLS verification.

        macOS Python may not bundle root certs; disabling verification is safe
        for known, trusted MCP endpoints.
        """
        import httpx
        return httpx.AsyncClient(
            verify=False,
            headers=headers or {},
            timeout=timeout or httpx.Timeout(30),
            auth=auth,
        )

    async def list_tools(self) -> list[dict]:
        from mcp.client.streamable_http import streamablehttp_client
        from mcp import ClientSession

        async with streamablehttp_client(
            self.url,
            httpx_client_factory=self._no_verify_factory,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                tools = [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema,
                    }
                    for tool in result.tools
                ]
                log.info(f"  {self.server_id}: fetched {len(tools)} tools via Streamable HTTP")
                return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        from mcp.client.streamable_http import streamablehttp_client
        from mcp import ClientSession

        async with streamablehttp_client(
            self.url,
            httpx_client_factory=self._no_verify_factory,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                # Normalise CallToolResult → dict with 'content' list so the
                # proxy result handler at the bottom of call_tool() can process it.
                content_blocks = []
                for block in result.content:
                    if hasattr(block, "type") and block.type == "text":
                        content_blocks.append({"type": "text", "text": block.text})
                    else:
                        content_blocks.append({"type": "text", "text": json.dumps(block.model_dump() if hasattr(block, "model_dump") else str(block))})
                return {"content": content_blocks}


# ─── Client registry ────────────────────────────────────────────────────────────

_clients: dict[str, StdioMCPClient | StreamableHttpMCPClient] = {}

# Per-server locks serializing restart_server() (and, incidentally, the
# lazy-start race in StdioMCPClient._ensure_started()) so two concurrent
# `restart <id>` control commands for the same server can't both terminate/
# spawn subprocesses at once and orphan one of them.
_restart_locks: dict[str, asyncio.Lock] = {}


def _restart_lock_for(server_id: str) -> asyncio.Lock:
    if server_id not in _restart_locks:
        _restart_locks[server_id] = asyncio.Lock()
    return _restart_locks[server_id]


def get_client(server_id: str) -> StdioMCPClient | StreamableHttpMCPClient:
    if server_id not in _clients:
        server_def = SERVER_DEFS.get(server_id)
        if not server_def:
            raise ValueError(f"Unknown server_id: {server_id}")
        if server_def.get("type") == "http":
            _clients[server_id] = StreamableHttpMCPClient(server_id, server_def)
        else:
            _clients[server_id] = StdioMCPClient(server_id, server_def)
    return _clients[server_id]


# ─── Tool name namespacing ───────────────────────────────────────────────────────

def proxy_tool_name(server_id: str, original_name: str) -> str:
    """Create a unique proxied tool name: server_id__original_name"""
    return f"{server_id}__{original_name}"


def parse_proxy_tool_name(proxied: str) -> tuple[str, str] | None:
    """Parse 'server_id__tool_name' back to (server_id, tool_name)"""
    parts = proxied.split("__", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


# ─── Capability loading ──────────────────────────────────────────────────────────

async def load_capability_tools(cap_name: str) -> list[dict]:
    """Fetch tool schemas from all servers in a capability, return proxied tool list."""
    if cap_name in active_capabilities and active_capabilities[cap_name]:
        return active_capabilities[cap_name]

    cap_def = CAPABILITIES.get(cap_name)
    if not cap_def:
        raise ValueError(f"Unknown capability: {cap_name}")

    server_ids: list[str] = cap_def.get("servers", [])
    all_tools: list[dict] = []

    for server_id in server_ids:
        if server_id in server_tool_cache and server_tool_cache[server_id]:
            tools = server_tool_cache[server_id]
        else:
            try:
                client = get_client(server_id)
                tools = await client.list_tools()
                if tools:
                    server_tool_cache[server_id] = tools
            except Exception as e:
                log.error(f"Failed to fetch tools from {server_id}: {e}")
                tools = []

        # Namespace tool names to avoid collisions
        for tool in tools:
            proxied = dict(tool)
            proxied["name"] = proxy_tool_name(server_id, tool["name"])
            proxied["description"] = f"[{server_id}] {tool.get('description', '')}"
            all_tools.append(proxied)

    active_capabilities[cap_name] = all_tools
    return all_tools


def unload_capability(cap_name: str) -> bool:
    if cap_name in active_capabilities:
        del active_capabilities[cap_name]
        return True
    return False


async def restart_server(server_id: str) -> str:
    """Kill and relaunch a stdio server's subprocess in place.

    WHY THIS EXISTS: `/mcp reconnect` (Claude Code's built-in command) only
    re-attaches the client's transport to an already-running stdio subprocess —
    it does not kill and relaunch it. So if a server's underlying script/binary
    is redeployed in place (same command/args, new code), reconnect never picks
    up the new version; only a full Claude Code session restart does, which
    loses conversation context. See GitHub issue anthropics/claude-code#24057.

    This closes that gap for servers that sit behind the gateway: terminate the
    existing subprocess, drop the cached client/tool-schema, and (if the
    capability is currently active) eagerly relaunch it and re-notify the
    client via tools/list_changed — all without the Claude Code session itself
    ever restarting.
    """
    server_def = SERVER_DEFS.get(server_id)
    if not server_def:
        return f"error: unknown server id '{server_id}'"
    if server_def.get("type") == "http":
        return (
            f"error: '{server_id}' is an HTTP server — restart only applies to "
            f"stdio servers (HTTP servers reconnect fresh on every call already)"
        )

    # Serialize concurrent restarts of the *same* server: without this, two
    # overlapping `restart <id>` commands could both terminate/spawn at once,
    # each losing track of the other's subprocess (an orphan leak). This also
    # guards against a restart racing a fresh lazy-start (get_client() ->
    # _ensure_started()) triggered by an unrelated in-flight tool call.
    async with _restart_lock_for(server_id):
        proc = child_processes.get(server_id)
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            log.info(f"Control: killed {server_id} subprocess for restart")

        # Drop cached client/process/tools so the next call rebuilds from scratch.
        _clients.pop(server_id, None)
        child_processes.pop(server_id, None)
        server_tool_cache.pop(server_id, None)

        cap_name = find_capability_by_server(server_id)
        if cap_name and cap_name in active_capabilities:
            # Was active — relaunch eagerly now and refresh the schema so any
            # new/changed tools from the redeployed code surface immediately.
            unload_capability(cap_name)
            try:
                tools = await load_capability_tools(cap_name)
            except Exception as e:  # noqa: BLE001
                # Reload failed: the capability is already unloaded (its tools
                # are gone from active_capabilities) but the client doesn't
                # know that yet. Notify anyway so it re-fetches tools/list and
                # doesn't keep calling now-dead tool names into a black hole.
                await _send_list_changed()
                return f"error: restarted {server_id} but failed to reload tools: {e}"
            notified = await _send_list_changed()
            if notified is not True:
                return notified  # error string from _send_list_changed
            log.info(f"Control: restarted {server_id} and refreshed {len(tools)} tools")
            return f"ok: restarted {server_id} ({len(tools)} tools)"

        log.info(f"Control: restarted {server_id} (not currently active; will start fresh on next use)")
        return f"ok: restarted {server_id} (will start fresh on next use)"


# ─── Out-of-band control channel ─────────────────────────────────────────────────

def _capture_active_session() -> None:
    """Stash the live ServerSession the first time a real request is handled.

    `app.request_context` is a contextvar populated only inside request handling;
    reading it here (top of list_tools/call_tool) captures the session so the
    background control task can send notifications from OUTSIDE any request.
    """
    global _active_session
    try:
        _active_session = app.request_context.session
    except Exception:
        # No request context yet (e.g. called directly in a unit test) — ignore.
        # request_context raises LookupError when the contextvar is unset.
        pass


async def _send_list_changed():
    """Send notifications/tools/list_changed on the captured session.

    Returns True on success, or an 'error: ...' string suitable as a control reply.
    """
    if _active_session is None:
        return "error: no active session yet (open a client and let it list tools first)"
    try:
        await _active_session.send_tool_list_changed()
        return True
    except Exception as e:  # noqa: BLE001
        return f"error: failed to notify session: {e}"


def parse_control_line(line: str) -> tuple[str, str | None]:
    """Pure parser for one control-protocol line → (command, argument|None).

    Command is lower-cased; argument is everything after the first whitespace
    (or None). Blank/whitespace-only lines yield ("", None).
    """
    parts = line.strip().split(maxsplit=1)
    if not parts:
        return "", None
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else None
    return cmd, arg


async def handle_control_command(line: str) -> str:
    """Execute one control command and return the reply text (no trailing newline)."""
    cmd, arg = parse_control_line(line)

    if cmd == "ping":
        return "pong"

    if cmd in ("enable", "disable"):
        if not arg:
            return f"error: {cmd} requires a server id"
        cap_name = find_capability_by_server(arg)
        if not cap_name:
            return f"error: no capability owns server '{arg}'"

        if cmd == "enable":
            try:
                tools = await load_capability_tools(cap_name)
            except Exception as e:  # noqa: BLE001
                return f"error: failed to load '{arg}': {e}"
            notified = await _send_list_changed()
            if notified is not True:
                return notified  # error string
            log.info(f"Control: enabled {arg} ({len(tools)} tools) via out-of-band command")
            return f"ok: enabled {arg} ({len(tools)} tools)"

        # disable
        unload_capability(cap_name)
        notified = await _send_list_changed()
        if notified is not True:
            return notified
        log.info(f"Control: disabled {arg} via out-of-band command")
        return f"ok: disabled {arg}"

    if cmd == "restart":
        if not arg:
            return "error: restart requires a server id"
        return await restart_server(arg)

    if cmd == "":
        return "error: empty command"

    return f"error: unknown command '{cmd}'"


async def _handle_control_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Serve one connected control client: newline-delimited request/reply loop."""
    try:
        while True:
            raw = await reader.readline()
            if not raw:  # client disconnected
                break
            line = raw.decode("utf-8", errors="replace")
            if not line.strip():
                continue
            try:
                reply = await handle_control_command(line)
            except Exception as e:  # noqa: BLE001 — never let one command crash the gateway
                log.warning(f"Control command error: {e}")
                reply = f"error: {e}"
            try:
                writer.write((reply + "\n").encode())
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                break
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:  # noqa: BLE001
        log.warning(f"Control client handler error: {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def run_control_server(sock_path: Path = None):
    """Background task: listen on the Unix control socket until cancelled."""
    sock_path = Path(sock_path) if sock_path else CONTROL_SOCK_PATH
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove a stale socket file from a previous (crashed) run.
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass

    server = await asyncio.start_unix_server(_handle_control_client, path=str(sock_path))
    log.info(f"Control socket listening at {sock_path}")
    try:
        async with server:
            await server.serve_forever()
    finally:
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        log.info("Control socket closed.")


# ─── MCP Server definition ───────────────────────────────────────────────────────

app = Server("vero-mcp-router")


# ─── MCPRouter class (thin wrapper for testing / external use) ───────────────────

class MCPRouter:
    """Thin wrapper around the module-level app for testing and external callers."""

    def __init__(self, config_path: str):
        # Config is already loaded at module level; accept path for API compatibility
        pass

    async def list_tools(self) -> list[types.Tool]:
        return await list_tools()


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """
    Returns:
    1. The 3 meta-tools (always present)
    2. Any tools from currently active capabilities
    """
    _capture_active_session()

    meta_tools = [
        types.Tool(
            name="list_capabilities",
            description=(
                "List all available MCP capability groups. "
                "Call this to see what tool sets can be activated, "
                "then use use_capability() to load the ones you need."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="use_capability",
            description=(
                "Load a capability group's tools into the current session. "
                "Describe what you need in natural language, e.g. '查台股收盤價' or 'search the web'. "
                "The router will match to the best capability and make those tools available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Natural language description of what you need to do",
                    },
                },
                "required": ["description"],
            },
        ),
        types.Tool(
            name="drop_capability",
            description=(
                "Unload a capability group to free context space. "
                "Pass the capability name as returned by list_capabilities()."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Capability name to unload (e.g. 'taiwan_stocks')",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="auto_use_capability",
            description=(
                "Auto-load the capability that owns a specific tool and immediately call it. "
                "Pass the full namespaced tool name like 'twstock__get_realtime_quote' plus its "
                "arguments dict. The router finds which capability owns that server, loads it, "
                "and proxies the actual tool call in one step — no separate use_capability() needed. "
                "If only a server prefix is given (e.g. 'twstock'), just loads the capability. "
                "USE THIS instead of calling capability tools directly — they won't be registered "
                "until the capability is loaded."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": (
                            "Full namespaced tool name (e.g. 'twstock__get_realtime_quote') "
                            "or server prefix (e.g. 'twstock') to just load the capability"
                        ),
                    },
                    "arguments": {
                        "type": "object",
                        "description": (
                            "Arguments to pass to the tool (e.g. {'stock_code': '2330'}). "
                            "Provide when calling a specific tool; omit to just load the capability."
                        ),
                    },
                },
                "required": ["tool_name"],
            },
        ),
        types.Tool(
            name="which_capability",
            description=(
                "Find the best matching capability for a natural language description — "
                "WITHOUT loading it. Use this to decide what to load before committing. "
                "E.g. 'I need to look up Taiwan stock prices' → returns 'taiwan_stocks'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Natural language description of what you need to do",
                    },
                },
                "required": ["description"],
            },
        ),
    ]

    # Collect all currently active proxy tools
    proxy_tools: list[types.Tool] = []
    for cap_name, tools in active_capabilities.items():
        for tool_dict in tools:
            # Reconstruct types.Tool from dict
            proxy_tools.append(
                types.Tool(
                    name=tool_dict["name"],
                    description=tool_dict.get("description", ""),
                    inputSchema=tool_dict.get("inputSchema", {"type": "object", "properties": {}}),
                )
            )

    return meta_tools + proxy_tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Route tool calls to meta-handlers or child MCP servers."""
    _capture_active_session()

    # ── Meta: list_capabilities ──────────────────────────────────────────────
    if name == "list_capabilities":
        lines = ["## Available Capability Groups\n"]
        for cap_name, cap_def in CAPABILITIES.items():
            status = "✅ ACTIVE" if cap_name in active_capabilities else "  inactive"
            servers = cap_def.get("servers", [])
            lines.append(
                f"**{cap_name}** {status}\n"
                f"  {cap_def.get('description', '')}\n"
                f"  servers: {', '.join(servers)}"
            )
            if cap_name in active_capabilities:
                # Show actual loaded tool names (up to 5)
                tool_names = [t["name"] for t in active_capabilities[cap_name]]
                sample = tool_names[:5]
                extra = len(tool_names) - 5
                line = "  example tools: " + ", ".join(f"`{t}`" for t in sample)
                if extra > 0:
                    line += f" (+{extra} more)"
                lines.append(line)
            else:
                # Show server prefix patterns so Claude knows the naming scheme
                prefixes = ", ".join(f"`{sid}__*`" for sid in servers)
                lines.append(f"  tool prefix: {prefixes}")
            lines.append("")
        lines.append(
            "Use `use_capability(description)` to activate by keyword, "
            "`auto_use_capability(tool_name)` to activate by tool/server name, "
            "or `which_capability(description)` to preview the match without loading."
        )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Meta: use_capability ─────────────────────────────────────────────────
    if name == "use_capability":
        description = arguments.get("description", "")
        cap_name = match_capability(description)

        if not cap_name:
            # Fallback: list all and ask Claude to pick
            cap_list = "\n".join(
                f"- **{k}**: {v.get('description', '')}"
                for k, v in CAPABILITIES.items()
            )
            return [types.TextContent(
                type="text",
                text=(
                    f"No capability matched '{description}'.\n\n"
                    f"Available capabilities:\n{cap_list}\n\n"
                    f"Try use_capability() again with the exact capability name, "
                    f"or describe your need differently."
                ),
            )]

        try:
            tools = await load_capability_tools(cap_name)
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"Error loading capability '{cap_name}': {e}",
            )]

        # Notify Claude Code that the tool list has changed so it re-fetches tools/list
        try:
            await app.request_context.session.send_tool_list_changed()
            log.info(f"Sent tools/list_changed notification after loading '{cap_name}'")
        except Exception as e:
            log.warning(f"Failed to send tool_list_changed notification: {e}")

        tool_names = [t["name"] for t in tools]
        return [types.TextContent(
            type="text",
            text=(
                f"✅ Capability **{cap_name}** loaded — {len(tools)} tools now available.\n\n"
                f"Tools:\n" + "\n".join(f"  - `{t}`" for t in tool_names)
            ),
        )]

    # ── Meta: drop_capability ────────────────────────────────────────────────
    if name == "drop_capability":
        cap_name = arguments.get("name", "")
        if unload_capability(cap_name):
            # Notify Claude Code that tool list has changed
            try:
                await app.request_context.session.send_tool_list_changed()
                log.info(f"Sent tools/list_changed notification after dropping '{cap_name}'")
            except Exception as e:
                log.warning(f"Failed to send tool_list_changed notification: {e}")
            return [types.TextContent(
                type="text",
                text=f"✅ Capability **{cap_name}** unloaded.",
            )]
        else:
            return [types.TextContent(
                type="text",
                text=f"Capability '{cap_name}' was not active.",
            )]

    # ── Meta: auto_use_capability ────────────────────────────────────────────
    if name == "auto_use_capability":
        tool_name = arguments.get("tool_name", "")
        tool_arguments = arguments.get("arguments", None)

        # Extract server_id and original tool name
        # "server_id__tool_name" → server_id="server_id", original="tool_name"
        # "server_id" (bare prefix) → server_id="server_id", original=None
        has_tool = "__" in tool_name
        server_id = tool_name.split("__", 1)[0] if has_tool else tool_name
        original_tool_name = tool_name.split("__", 1)[1] if has_tool else None

        cap_name = find_capability_by_server(server_id)
        if not cap_name:
            cap_list = "\n".join(
                f"- **{k}** (servers: {', '.join(v.get('servers', []))})"
                for k, v in CAPABILITIES.items()
            )
            return [types.TextContent(
                type="text",
                text=(
                    f"No capability found for server/tool '{tool_name}'.\n\n"
                    f"Available capabilities:\n{cap_list}\n\n"
                    f"Use list_capabilities() to see full details."
                ),
            )]

        try:
            tools = await load_capability_tools(cap_name)
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"Error loading capability '{cap_name}': {e}",
            )]

        try:
            await app.request_context.session.send_tool_list_changed()
            log.info(f"Sent tools/list_changed notification after auto-loading '{cap_name}'")
        except Exception as e:
            log.warning(f"Failed to send tool_list_changed notification: {e}")

        # If a specific tool name was given (server__tool), proxy the call immediately
        if has_tool and original_tool_name is not None:
            call_args = tool_arguments if isinstance(tool_arguments, dict) else {}
            log.info(f"Auto-routing: proxying {server_id}/{original_tool_name} with args={call_args}")
            try:
                client = get_client(server_id)
                result = await client.call_tool(original_tool_name, call_args)
            except Exception as e:
                log.error(f"Auto-proxied call failed: {server_id}/{original_tool_name}: {e}")
                return [types.TextContent(
                    type="text",
                    text=f"Loaded capability '{cap_name}' but tool call failed: {e}",
                )]

            # Normalize result to text (same logic as the proxy handler below)
            if isinstance(result, dict):
                content = result.get("content", [])
                if isinstance(content, list):
                    texts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                        else:
                            texts.append(json.dumps(block))
                    return [types.TextContent(type="text", text="\n".join(texts))]
                return [types.TextContent(type="text", text=json.dumps(result))]
            elif isinstance(result, str):
                return [types.TextContent(type="text", text=result)]
            else:
                return [types.TextContent(type="text", text=json.dumps(result))]

        # Bare server prefix — just load the capability, no immediate call
        return [types.TextContent(
            type="text",
            text=f"✅ Loaded [{cap_name}] for server [{server_id}]. {len(tools)} tools now available.",
        )]

    # ── Meta: which_capability ───────────────────────────────────────────────
    if name == "which_capability":
        description = arguments.get("description", "")
        cap_name = match_capability(description)

        if not cap_name:
            cap_list = "\n".join(
                f"- **{k}**: {v.get('description', '')}"
                for k, v in CAPABILITIES.items()
            )
            return [types.TextContent(
                type="text",
                text=(
                    f"No capability matched '{description}'.\n\n"
                    f"Available capabilities:\n{cap_list}"
                ),
            )]

        cap_def = CAPABILITIES[cap_name]
        servers = cap_def.get("servers", [])
        prefixes = ", ".join(f"`{sid}__*`" for sid in servers)
        return [types.TextContent(
            type="text",
            text=(
                f"**{cap_name}**\n"
                f"{cap_def.get('description', '')}\n"
                f"Servers: {', '.join(servers)}\n"
                f"Tool prefix: {prefixes}\n\n"
                f"Call `use_capability(\"{cap_name}\")` or "
                f"`auto_use_capability(\"{servers[0] if servers else cap_name}\")` to load it."
            ),
        )]

    # ── Proxy: route to child MCP ────────────────────────────────────────────
    parsed = parse_proxy_tool_name(name)
    if parsed:
        server_id, original_tool_name = parsed
        try:
            client = get_client(server_id)
            result = await client.call_tool(original_tool_name, arguments)
        except Exception as e:
            log.error(f"Child MCP call failed: {server_id}/{original_tool_name}: {e}")
            return [types.TextContent(
                type="text",
                text=f"Error calling {server_id}/{original_tool_name}: {e}",
            )]

        # Normalize result to text
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                # MCP content blocks
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    else:
                        texts.append(json.dumps(block))
                return [types.TextContent(type="text", text="\n".join(texts))]
            return [types.TextContent(type="text", text=json.dumps(result))]
        elif isinstance(result, str):
            return [types.TextContent(type="text", text=result)]
        else:
            return [types.TextContent(type="text", text=json.dumps(result))]

    # Unknown tool
    return [types.TextContent(
        type="text",
        text=f"Unknown tool: {name}. Use list_capabilities() to see available tools.",
    )]


# ─── Cleanup ─────────────────────────────────────────────────────────────────────

async def shutdown():
    """Terminate all child processes gracefully."""
    for server_id, proc in child_processes.items():
        if proc.returncode is None:
            log.info(f"Terminating child MCP: {server_id}")
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()


# ─── Entrypoint ──────────────────────────────────────────────────────────────────

async def main():
    log.info("Vero MCP Router starting...")
    log.info(f"Config: {len(CAPABILITIES)} capabilities, {len(SERVER_DEFS)} servers")
    log.info(f"Log: {log_path}")

    try:
        async with stdio_server() as (read_stream, write_stream):
            # Declare tools_changed=True so Claude Code knows we can send
            # notifications/tools/list_changed when capabilities are loaded/dropped
            init_options = app.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)
            )
            # Start the out-of-band control socket alongside the MCP server so an
            # external `mcp enable <server>` can flip capabilities live.
            control_task = asyncio.create_task(run_control_server())
            try:
                await app.run(read_stream, write_stream, init_options)
            finally:
                control_task.cancel()
                try:
                    await control_task
                except asyncio.CancelledError:
                    pass
    finally:
        await shutdown()
        log.info("Vero MCP Router stopped.")


if __name__ == "__main__":
    asyncio.run(main())
