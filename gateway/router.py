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
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


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


# ─── MCP Server definition ───────────────────────────────────────────────────────

app = Server("vero-mcp-router")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """
    Returns:
    1. The 3 meta-tools (always present)
    2. Any tools from currently active capabilities
    """
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

    # ── Meta: list_capabilities ──────────────────────────────────────────────
    if name == "list_capabilities":
        lines = ["## Available Capability Groups\n"]
        for cap_name, cap_def in CAPABILITIES.items():
            status = "✅ ACTIVE" if cap_name in active_capabilities else "  inactive"
            servers = ", ".join(cap_def.get("servers", []))
            lines.append(
                f"**{cap_name}** {status}\n"
                f"  {cap_def.get('description', '')}\n"
                f"  servers: {servers}\n"
            )
        lines.append(
            "\nUse `use_capability(description)` to activate a group."
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
            await app.run(read_stream, write_stream, init_options)
    finally:
        await shutdown()
        log.info("Vero MCP Router stopped.")


if __name__ == "__main__":
    asyncio.run(main())
