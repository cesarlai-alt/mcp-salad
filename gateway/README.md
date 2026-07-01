# MCP Salad Gateway

Dynamic tool loading for MCP — load servers on demand, free context when done.

## The problem

Every MCP server injects its full tool schema at startup. 11 servers = 400+ tools permanently in context. One Taiwan stock server alone ships 161 tools (~8,000 tokens) before you've typed a word.

## How it works

Gateway exposes 3 meta-tools:

| Tool | What it does |
|------|-------------|
| `list_capabilities` | Show available servers and their status |
| `use_capability(description)` | Natural language → spawn server → inject tools |
| `drop_capability(name)` | Unload server, free context |

When you call `use_capability("taiwan stocks")`, Gateway spawns the server, fetches its schemas, and sends `notifications/tools/list_changed`. Claude Code re-fetches the tool list — 161 tools appear instantly, no restart.

## Install

```bash
pip install mcp PyYAML
cp config.example.yaml config.yaml
# Edit config.yaml with your API keys
bash install.sh
```

Add to `~/.mcp.json`:
```json
{
  "mcpServers": {
    "mcp-salad-gateway": {
      "command": "python3",
      "args": ["/path/to/mcp-salad/gateway/router.py"]
    }
  }
}
```

## Protocol notes

Two bugs we fixed that affect most MCP child servers:

1. `notifications/initialized` must be sent **without** an `id` field — it's a notification, not a request. Sending with `id` causes some servers to return `-32601 Method Not Found`.

2. Child servers send unsolicited notifications between request and response. Match on `response["id"]`, skip notifications (no id) and wrong-id messages.

See the main [MCP Salad README](../README.md) to find and install servers via the registry.
