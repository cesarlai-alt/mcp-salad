#!/usr/bin/env python3
"""
A minimal but REAL stdio MCP server, used by the router integration tests.

It uses the exact same `mcp.server` low-level API that gateway/router.py speaks
to over stdio, so the router can spawn it as a child and exercise the real MCP
protocol (initialize handshake, tools/list, tools/call).

Exposes two trivial tools:
  - echo(text)  → returns `text` verbatim
  - add(a, b)   → returns a + b as text

Run standalone:
    python3 tests/fixtures/fake_mcp_server.py
starts an stdio MCP server that talks JSON-RPC over stdin/stdout.
"""

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("fake-mcp-server")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="echo",
            description="Echo the given text back unchanged.",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        types.Tool(
            name="add",
            description="Add two numbers and return the sum.",
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "echo":
        return [types.TextContent(type="text", text=str(arguments.get("text", "")))]
    if name == "add":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        total = a + b
        # Emit an integer-looking result when both inputs are whole numbers.
        if isinstance(total, float) and total.is_integer():
            total = int(total)
        return [types.TextContent(type="text", text=str(total))]
    # Unknown tool: raising here exercises the server's own error path; the
    # low-level MCP server converts this into an isError CallToolResult rather
    # than a transport crash, which the router must handle gracefully.
    raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
