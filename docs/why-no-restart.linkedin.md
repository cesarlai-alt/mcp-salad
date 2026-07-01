Every MCP gateway I tried made me restart the client to add or toggle a server. Quit the app, lose the session, re-explain what I was doing. Everyone just accepts this tax. I did too — until I actually read the spec.

MCP already defines a fix: `notifications/tools/list_changed`. It's a server-to-client push that says "my tool list changed, go re-fetch it." The client re-reads the catalog on the live connection. No restart. Almost nobody wires it up end to end.

So I did. With MCP Salad you can run `salad enable twstock` in one terminal, and a Claude Code session running in another terminal gains 161 new tools instantly. `disable` and they leave just as cleanly.

The one genuinely tricky part: that notification normally has to be sent from inside a request. But my "enable" command arrives out-of-band, over a control socket, with no request context to grab the session from. The fix is unglamorous — capture the live session the first time the client lists tools, stash it, and push notifications on it later. That's the whole trick.

Why it matters beyond skipping a relaunch: every tool a model knows about costs context tokens, whether you call it or not. One server = ~8k tokens. Hot-swap turns tools into something you load on demand and hand back — a real-time knob on your context budget. 🥗

I'm a global-business exec who codes on the side, so I'll be honest: the MCP authors did the hard part. I just wired it up. The client has to honor the notification, and the control socket is POSIX-only for now.

Repo + write-up: github.com/cesarlai-alt/mcp-salad

If you build MCP gateways — are you sending `tools/list_changed`? And which clients actually honor it? Curious what people have seen.
