I built a small tool for Claude Code that lets you flip MCP servers on and off mid-session — no restart.

Second terminal, one command: `salad enable firecrawl`. Your running session gains the tools instantly. `salad disable firecrawl` and they're gone, context tokens returned. The mechanism is the MCP spec's own `notifications/tools/list_changed` — nothing I invented, just wired end-to-end with a clean CLI.

The practical motivation: one popular MCP server ships 161 tools and ~8,000 tokens. You don't always need them. Being able to load on demand and unload when done adds up.

The registry side stands on giants — `salad search` and `salad install` route through the official MCP registry (Anthropic + GitHub + Microsoft, 14,000+ servers). I'm not trying to out-register Smithery or Glama. The actual add is operator UX: flip a packaged server on/off live, from a second terminal, in a session that's already running.

Prior art exists — mizchi/mcp-reloader hot-reloads during the dev-edit loop, MetaMCP does dynamic server aggregation. This targets a slightly different moment: you, as the operator, deciding mid-session which tools you need.

Honest framing: solo proof-of-concept, days old, built as a learning exercise. Whether it's useful beyond that, I don't know yet.

Repo: https://github.com/cesarlai-alt/mcp-salad

#MCP #ClaudeCode #BuildInPublic
