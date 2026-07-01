Title: Show HN: MCP Salad – hot-swap MCP servers in a running Claude Code session

MCP Salad doesn't grow vegetables. It's the bowl.

From a second terminal: `salad enable firecrawl`. Your already-running Claude Code session gains those tools instantly. `salad disable firecrawl` and they're gone. No restart.

The mechanism is the MCP spec's own `notifications/tools/list_changed` notification — nothing I invented. Others use it too: mizchi/mcp-reloader does file-watch hot-reload aimed at the dev-edit loop (you're iterating on server code), MetaMCP does dynamic aggregation into namespaces, Docker's MCP Toolkit has a "Dynamic MCPs" feature. The distinction here is the operator UX: you're not editing server code, you're flipping whole packaged servers on/off mid-session from a separate terminal. Small distinction, real difference in workflow.

Why bother? One popular server ships 161 tools, roughly 8,000 tokens. If you don't need it right now, those tokens are just gone. With MCP Salad, servers aren't in your context until you enable them — and you can hand those tokens back when you're done.

The registry half is deliberately thin. There's an official MCP registry backed by Anthropic, GitHub, and Microsoft (14,000+ servers), plus Smithery, Glama, and mcp.so indexing tens of thousands more. MCP Salad isn't trying to compete there — `salad search` and `salad install` route straight through registry.modelcontextprotocol.io. The 26 curated local entries are just servers I actually use, pre-tested, with credentials surfaced as placeholders rather than buried in docs.

Honest caveats: "no restart" depends on the client honoring `list_changed`. Recent Claude Code does. Claude Desktop was still ignoring it a few months ago. I haven't tested Cursor or Windsurf. The gateway is Python, MIT, self-hosted. No server-side component, no account.

Repo: https://github.com/cesarlai-alt/mcp-salad

Solo project, days old. Feedback welcome — especially if you've dealt with MCP context bloat, know the `list_changed` landscape better than I do, or think the operator vs. developer hot-swap distinction isn't real.
