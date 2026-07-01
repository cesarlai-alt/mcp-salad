# Why MCP Salad doesn't make you restart

Almost every MCP gateway I've tried has the same tax: add a server, toggle one on, change your mind — and you restart the client. Quit Claude Code, reopen it, lose your session, re-explain what you were doing. Everyone just accepts this. I did too, until I read the part of the MCP spec that says you don't have to.

This is a write-up of what that restart actually is, the one notification that removes it, and the annoying engineering detail you hit when you try to fire that notification at the right moment. I'm not a career engineer — I do global business for a living and build tools on the side — so this is written for the person who just wanted their tools to show up without quitting the app. The spec did the hard part; my job was mostly not to get in its way.

## Contents

- [Why the restart exists](#why-the-restart-exists)
- [The one line in the spec that changes everything](#the-one-line-in-the-spec-that-changes-everything)
- [The gotcha nobody talks about](#the-gotcha-nobody-talks-about)
- [Before and after: `salad enable twstock`](#before-and-after-salad-enable-twstock)
- [Why this matters: context is the budget](#why-this-matters-context-is-the-budget)
- [Honest limitations](#honest-limitations)

## Why the restart exists

When an MCP client (Claude Code, Cursor, Windsurf, anything spec-compatible) connects to a server, it does a handshake: `initialize`, then `tools/list`. It fetches the catalog of tools the server offers *once*, at the start, and caches it. From then on it assumes that catalog is stable.

That assumption is why editing your `mcp.json` needs a restart. You changed the catalog on disk, but the running client already read the old one and has no reason to look again. The only guaranteed way to make it re-read is to tear the connection down and redo the handshake — i.e., restart.

Most gateways inherit this. A gateway is itself an MCP server to the client, so it too presents a tool list at init. Add a downstream server to the gateway and the client doesn't know — it's still holding the tool list from the last handshake.

## The one line in the spec that changes everything

The MCP spec already anticipated this. A server can tell the client "my tool list is no longer what you cached — go fetch it again." The message is:

```
notifications/tools/list_changed
```

It's a server-to-client notification (no request, no response — a push). When the client receives it, a compliant client re-issues `tools/list` and swaps in the new catalog on the live connection. No teardown, no restart.

There's a catch that trips people up: you only get to send it if you *declared you would* at handshake time. In the MCP Python SDK that's a one-liner when you build the server's initialization options:

```python
from mcp.server.lowlevel.server import NotificationOptions

init_options = app.create_initialization_options(
    notification_options=NotificationOptions(tools_changed=True)
)
await app.run(read_stream, write_stream, init_options)
```

`tools_changed=True` puts `listChanged: true` in the server's advertised capabilities. Skip it and the client is within its rights to ignore your notification — you promised a static tool list, so it stops listening. This is the single most common reason "I sent the notification and nothing happened."

So the mechanism is: declare `tools_changed=True`, then call `session.send_tool_list_changed()` whenever your catalog changes. That's the whole trick. Underused, not hard.

## The gotcha nobody talks about

Here's where it got interesting for me.

Inside a normal tool call, sending the notification is trivial. When Claude calls `use_capability(...)` and I load a server's tools, I'm *inside a request*, so I have the session right there:

```python
await app.request_context.session.send_tool_list_changed()
```

`app.request_context` is a Python `contextvar`. The SDK sets it while a request is being handled and clears it afterward. Perfect for the in-band case.

But MCP Salad's whole pitch is that you can flip a server on **from another terminal** — `salad enable twstock` — while a session sits idle. That command doesn't arrive as an MCP request. It comes in over a separate Unix-domain control socket the gateway listens on in a background task, and that task runs *outside* any request context. So `app.request_context` is unset there — reading it throws `LookupError`. There's no session to grab.

The fix is unglamorous and works: capture the live session the first time a real request *does* come through, and stash it. The client issues `tools/list` during initialization, within milliseconds of connecting — so by the time anyone types `salad enable`, the session has long since been captured.

```python
_active_session = None

def _capture_active_session() -> None:
    """Stash the live ServerSession the first time a real request is handled."""
    global _active_session
    try:
        _active_session = app.request_context.session
    except Exception:
        # No request context yet (unit test / pre-init) — ignore.
        pass
```

I call `_capture_active_session()` at the top of both the `list_tools` and `call_tool` handlers. Then the background control task pushes the notification on that stashed object, no request needed:

```python
async def _send_list_changed():
    if _active_session is None:
        return "error: no active session yet (open a client and let it list tools first)"
    await _active_session.send_tool_list_changed()
    return True
```

So the out-of-band flow is: `salad enable twstock` opens the control socket → the gateway loads that server's tools into the active capability set → `_send_list_changed()` fires `send_tool_list_changed()` on the captured session → the client re-fetches `tools/list` → the new tools appear in a session that was already running.

That's the one non-obvious bit of engineering in the whole project: a notification has to originate from a code path that has no natural handle on the connection, so you keep a handle from earlier and reuse it.

## Before and after: `salad enable twstock`

Two terminal windows. Left: a running Claude Code session. Right: your shell.

**Before** — you ask about a Taiwan stock. The session has no such tool, because you never loaded that server. Old world: edit config, quit, relaunch, re-ask.

**After:**

```bash
# right-hand terminal, session on the left still running
$ salad enable twstock
ok: enabled twstock (161 tools)
```

Back on the left, without touching it, you ask again — and the model now has the quote tool. No restart, no new chat. The `enable` command is a thin socket client; it writes one line to the gateway and prints the reply:

```python
@cli.command()
@click.argument("server")
def enable(server):
    """Enable a server in a RUNNING Gateway (no restart) — live tool injection."""
    _run_control_command("enable", server)
```

`disable` is the mirror image: it drops the capability and sends the same notification, so the tools *leave* a running session just as cleanly as they arrived.

## Why this matters: context is the budget

If this were only about skipping a relaunch, it'd be a convenience. It's more than that because of what it lets you do with context.

Every tool a client knows about costs tokens — its name, description, and JSON schema all sit in the model's context whether or not you ever call it. One popular server in the registry ships **161 tools, roughly 8k tokens**. Load ten servers like that and you've spent a big chunk of your window before you've asked anything.

Hot-swap turns tools into something you load on demand and, crucially, hand back:

- `enable` when you need a capability — the tools stream into the running session.
- `disable` when you're done — the capability is dropped and the tools disappear, returning the context.

Because both directions ride the same `tools/list_changed` notification, this is a real-time knob on your context budget, not a restart-gated one. You keep a lean default and reach for the heavy servers only for the turns that need them.

## Honest limitations

I want to be straight about the edges:

- **The client has to honor the notification.** This is a spec feature, not magic. A client that ignores `notifications/tools/list_changed` won't hot-swap no matter what the gateway does. Claude Code re-fetches on the notification in day-to-day use — that's how the gateway's in-session capability loading already works, and `salad enable` fires the same `send_tool_list_changed()` down the same path. Other clients vary — treat it as "works where the client implements the spec."
- **The control socket is POSIX-only today.** It's a Unix-domain socket, so macOS and Linux. A Windows port would need a different transport — a named pipe or TCP on loopback. Not hard, just not done.
- **The session-capture is a pragmatic hack, not a framework feature.** It relies on a real request arriving before the first out-of-band command. In practice the init-time `tools/list` guarantees that, but if you managed to fire `enable` before any client ever connected, you'd get a clean "no active session yet" error rather than a hot-swap.

None of this is revolutionary, and I don't want to oversell it. The MCP authors wrote the notification and the SDK exposed it; MCP Salad just wires it end to end and adds the out-of-band control channel so you can drive it from your shell. The interesting part isn't the code — it's that the spec already lets you do this, and almost nothing does.

## Try it

Repo: **[github.com/cesarlai-alt/mcp-salad](https://github.com/cesarlai-alt/mcp-salad)**

If you're building an MCP gateway, wire up `tools_changed=True` and `send_tool_list_changed()`. Your users will stop restarting. If you try MCP Salad and it doesn't hot-swap on your client, open an issue — I'd like to know which clients honor the notification and which don't.
