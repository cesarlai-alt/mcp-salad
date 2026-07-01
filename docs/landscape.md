# Where MCP Salad sits — an honest landscape

*Last scanned: 2026-07-01. This space moves weekly; treat numbers as "as of" snapshots, not precise counts.*

MCP Salad is a tiny, days-old, single-author project. This page exists so nobody — including its author — overclaims what it is. The short version: the ideas here are already occupied by larger, better-backed players. What's genuinely ours is a clean **operator hot-swap UX**, and the fact that it works at all.

## The landscape

| Player | Type | Scale (as of 2026) | Runtime hot-swap via `list_changed`? | Hosted / OSS | Backing |
|---|---|---|---|---|---|
| **Official MCP Registry** (registry.modelcontextprotocol.io) | Registry (metadata API) | Preview since 2025-09; not yet GA | N/A — metadata only, runs nothing | OSS + hosted API | **Anthropic + GitHub + Microsoft + PulseMCP** |
| **Smithery** (smithery.ai) | Registry + hosting | 6,000+ listed (~3,000 hosted) | No | Hosted + OSS CLI | VC-backed startup |
| **MetaMCP** (metatool-ai/metamcp) | Aggregator / gateway / middleware | ~1–2k GitHub stars, growing | Partial — dynamic aggregation into namespaces; not an enable-from-terminal UX | Self-host (Docker), OSS | Community / small team |
| **mcp.so** | Directory (listing only) | ~20,000+ servers | No | Community site | chatmcp community |
| **PulseMCP** (pulsemcp.com) | Directory | ~12–20k servers, curated | No | Hosted directory | Independent; co-builder of the official registry |
| **Glama** (glama.ai/mcp) | Directory + gateway + chat | ~50k servers | No — gateway = hosting/access-control front | Hosted + self-own gateway | Independent |
| **Docker MCP Catalog/Toolkit/Gateway** | Curated catalog + gateway | 270–300+ verified servers | Partial — ships a "Dynamic MCPs" feature | OSS gateway + hosted catalog | **Docker, Inc.** |
| **RaiAnsar/mcp-gateway** | Lazy-loading proxy | small OSS | On-demand loading, ~95% token reduction | OSS | Solo |
| **mizchi/mcp-reloader** | Hot-reload dev proxy | small OSS | **Yes** — file-watch + `tools/list_changed` (dev-loop focused) | OSS | Solo |
| **MCP Salad** (this repo) | Tiny registry + on-demand gateway with hot-swap | **26 servers, days old** | **Yes, by design** — `salad enable` from a 2nd terminal → live tools via `list_changed` | OSS (MIT), self-host | **Single author** |

## The blunt verdict

1. **The registry half is redundant.** There's an *official* MCP registry backed by Anthropic, GitHub, Microsoft and PulseMCP (launched 2025-09), plus directories indexing tens of thousands of servers (mcp.so, PulseMCP, Glama). A hand-curated 26-entry YAML registry does not compete with that.
2. **The "load servers on demand to save context" gateway idea is not novel** — Docker's Dynamic MCPs, MetaMCP, and RaiAnsar/mcp-gateway already do it.
3. **Even the `list_changed` hot-swap has prior art** — mizchi/mcp-reloader watches files and emits `tools/list_changed` to hot-reload into a session.
4. **The headline "no restart" depends on a client we don't control.** Whether it works hinges on the *client* honoring `list_changed`. Recent Claude Code does (support landed through 2026); Claude Desktop was still ignoring it as of 2026-04. So "no restart" is true on some clients/versions and false on others — never promise it universally.

## What's genuinely ours (strict)

- **The operator hot-swap UX**: `salad enable <server>` from a *separate terminal*, over a unix socket, flipping a whole packaged server on/off in a *running* Claude Code session. mizchi/mcp-reloader targets the dev-edit loop (you're editing server code); Salad targets the operator loop (turn a packaged server on/off mid-session). Uncommon and clean — not unique.
- **Small-surface simplicity**: one person, Python, MIT, a doctor/upgrade/publish CLI, curated servers. More legible than MetaMCP's Docker/namespace/OIDC machinery for a personal Claude-Code-native workflow.
- **It's a real, working personal feat** — a working MCP gateway with live hot-swap, built solo in ~a day. That's a credibility/portfolio asset, not a market position.

Everything else (on-demand loading, token savings, a YAML registry, an install/list/publish CLI) is generic and done bigger elsewhere. Don't claim those as innovations.

## Strategic options

1. **Niche hard on the hot-swap operator-UX for Claude Code.** Drop the "registry" positioning; be "the cleanest live enable/disable of MCP servers inside a running Claude Code session, from your terminal." Own the ergonomics + token-context angle. Modest but real odds of a small following.
2. **Stop maintaining a rival registry; consume the official one.** Point `salad search/install` at registry.modelcontextprotocol.io. Removes the weakest, most-redundant surface and makes Salad a good ecosystem citizen. Cheap, honest, upside-only.
3. **Contribute the hot-swap upstream instead of competing.** The live-`list_changed` operator flow maps to open Claude Code issues (#4118, #13646, #31893). A crisp reference implementation or write-up on those threads is higher-leverage for getting Anthropic's attention than a standalone 0-star repo.
4. **Treat it explicitly as a learning/portfolio piece.** Frame publicly as "I shipped a working MCP gateway in a day," not "a Smithery/MetaMCP alternative." Zero embarrassment risk, real reputational value, truest story.

**Realistic read:** options 2 + 3 combined (be a good citizen + push the idea upstream) is the highest-value path. Option 1 is a fine hobby. There is no honest path where a days-old solo project displaces Smithery / Glama / Docker / the official registry.

## Safe things to say in public

- *"It's not trying to be Smithery or the official registry — those index tens of thousands of servers and are backed by Anthropic and GitHub. MCP Salad is a tiny personal gateway focused on one thing: flipping MCP servers on/off live inside a running Claude Code session, from a second terminal, no restart."*
- *"The mechanism is just the MCP spec's `list_changed` notification — nothing I invented. What I care about is the operator UX around it. Whether 'no restart' works depends on your client, which even Claude Code only fully sorted out during 2026."*
- *"It's a solo, days-old project — a working proof-of-concept and a learning exercise, not a product competing with funded platforms."*

## Caveats on this scan

Directory counts are inflated by auto-crawled junk — treat as "tens of thousands, quality-varying." MetaMCP star counts varied by source (~1–2k). Funding for Smithery/Glama unconfirmed. Claude Code's `list_changed` status is version-specific and still evolving — verify against the exact version you demo on before any public "no restart" claim.

*Sources: official MCP registry blog & registry.modelcontextprotocol.io, smithery.ai, metatool-ai/metamcp, mcp.so, pulsemcp.com, glama.ai/mcp, Docker MCP Catalog docs, mizchi/mcp-reloader, RaiAnsar/mcp-gateway, Claude Code issues #4118 / #13646 / #50339.*
