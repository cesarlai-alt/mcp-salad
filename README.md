[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/cesarlai-alt-mcp-salad-badge.png)](https://mseep.ai/app/cesarlai-alt-mcp-salad)

# MCP Salad 🥗

### The bowl, not the vegetables.

You start a session. You don't know which tools you'll need.

Every other MCP setup makes you decide upfront — edit a config file, restart the client, repeat until you've bloated your context with tools you're not using. MCP Salad doesn't work that way. Servers sit dormant until you need them. When you do, one command drops them into your live session. Done with them? Remove them and the context comes back.

**No restart. No pre-planning. No context waste.**

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Servers](https://img.shields.io/badge/curated_servers-103-brightgreen.svg)
![Registry](https://img.shields.io/badge/official_registry-14%2C000%2B-ff69b4.svg)

---

## The complete loop

```bash
# 1. Don't know what you need? Describe it.
salad suggest "research patents and prior art"

#   → Top 5 matches (searches 14,373 servers):
#     1. lens-mcp          [official]  Patent search — USPTO, EPO, WIPO
#     2. semantic-scholar  [official]  200M+ academic papers, citations
#     3. arxiv-mcp         [official]  Preprint papers, full-text access
#     4. pubmed            [curated]   Biomedical literature (NIH)
#     5. crossref-mcp      [official]  DOI lookup, citation metadata

# 2. Install it.
salad install lens-mcp

# 3. Drop it into your running session — no restart.
salad enable lens-mcp
#   → ✓ tools live in session. Claude can now search patents.

# 4. Done with it? Take the context back.
salad disable lens-mcp
#   → ✓ disabled. Context freed.
```

That's the loop. Suggest → install → enable → use → disable. Mid-session, no restart, no config editing.

![MCP Salad demo](docs/hotswap-demo.gif)

---

## Why this matters

One popular MCP server ships **161 tools — roughly 8,000 tokens** of context. If you're not using it, that's 8k tokens doing nothing but slowing you down every turn.

With MCP Salad, you load a server only when you need it. When you're done, you unload it. Your session stays lean.

|                              | Restart required | Context cost when idle | Load on demand | Unload mid-session |
|------------------------------|:----------------:|:----------------------:|:--------------:|:------------------:|
| Edit `mcp.json` by hand      |        ✅         |          ✅             |       ❌        |         ❌          |
| Most MCP gateways            |        ✅         |          ✅             |       ❌        |         ❌          |
| **MCP Salad**                |        ❌         |          ❌             |       ✅        |         ✅          |

Hot-swap works by wiring up the MCP spec's own `notifications/tools/list_changed` end-to-end. The spec defines it; MCP Salad uses it cleanly.

---

## Discovery: `salad suggest`

The hardest part of MCP isn't running servers — it's knowing which ones exist.

```bash
salad suggest "analyze financial statements"
salad suggest "check drug interactions"
salad suggest "track container shipments"
salad suggest "查台灣股票"                   # CJK works too
```

Each query searches 14,373 servers from the official MCP registry. No LLM, no API key — pure keyword matching, scored by relevance. Top 5 results back in seconds. Pick one, install it, enable it.

---

## 103 curated servers across 15 domains

Beyond the official registry, MCP Salad ships with 103 hand-picked servers organized by domain. These are tested, documented, and tagged — not a raw dump.

| Domain | Servers | Examples |
|--------|---------|---------|
| 🧬 Medical & Pharma | 16 | PubMed, ClinicalTrials.gov, OpenFDA, ICD-10, RxNorm, ChEMBL, UniProt |
| 💹 Finance & Economics | 14 | FRED, IMF, OECD, World Bank, ECB, SEC EDGAR, US Treasury |
| 🚢 Trade & Customs | 6 | UN Comtrade, HTS tariff codes, HS classifier, PortWatch |
| ⚖️ Legal & Compliance | 8 | Taiwan/Brazil/Japan law, OFAC sanctions, VAT validator |
| 🔬 Research | 8 | Semantic Scholar, arXiv, CrossRef, OpenAlex, CORE |
| 📋 Patents & IP | 4 | USPTO, EPO, Lens, Google Patents |
| ✈️ Travel | 5 | Flight data, Amadeus, visa info |
| 📄 Documents | 6 | Word, Excel, PDF, Markdown converters |
| 🗺️ Geo | 4 | Geocoding, elevation, place search |
| 🛠️ Developer Tools | 12 | Firecrawl, Context7, Git, GitHub, code execution |
| 🎵 Audio & Media | 4 | Spotify, YouTube, audio analysis |
| 🗄️ Databases | 5 | PostgreSQL, MySQL, SQLite, Redis |
| ⚙️ Infrastructure | 4 | Docker, K8s, AWS, monitoring |
| 📚 Reference | 4 | Wikipedia, Wikidata, OpenLibrary |
| 🔧 Productivity | 3 | Obsidian, Notion, Calendar |

Full catalog with install instructions: [`registry/CATEGORIES.md`](registry/CATEGORIES.md)

---

## `salad search` spans both sources

```bash
salad search biomedical            # curated + official registry
salad search biomedical --source local    # curated only, fast
salad search biomedical --source official # official registry only
```

Curated results are tagged `[curated]`. Official hits are tagged `[official]`. Install any of them the same way: `salad install <name>`.

---

## Quick reference

```bash
# Discovery
salad suggest "<description>"      # keyword-match 14k+ servers, top 5
salad search <query>               # search curated + official registry

# Management
salad install <name>               # add to your gateway config
salad uninstall <name>             # remove from config
salad list                         # what's installed
salad doctor                       # health-check all servers

# Runtime (hot-swap — no restart)
salad enable <name>                # load into running session
salad disable <name>               # unload, free context

# Contribute
salad publish                      # submit your server in ~30s
```

---

## Install

```bash
git clone https://github.com/cesarlai-alt/mcp-salad
cd mcp-salad
ln -s "$(pwd)/salad" /usr/local/bin/salad
salad --help
```

Copy `gateway/config.example.yaml` to `~/.mcp-salad/config.yaml`, then point your MCP client at the gateway socket (`~/.mcp-salad/gateway.sock`).

**Dependencies:**

```bash
pip install pyyaml click
```

> Hot-swap requires your client to honor `notifications/tools/list_changed`. Claude Code (recent versions) does. Cursor, Windsurf: untested. The registry/CLI works with any MCP-compatible client regardless.

---

## How it compares (honest version)

This is a small, solo project. There's an official MCP registry (Anthropic + GitHub + Microsoft) with tens of thousands of servers — MCP Salad isn't trying to out-register those; `salad suggest` searches them directly. The `list_changed` hot-swap mechanism is from the MCP spec itself; others use it too. What MCP Salad adds is **a clean operator UX**: one command to flip a server on or off in a live session, with a discovery layer to find what you need first.

For the full competitive landscape: [`docs/landscape.md`](docs/landscape.md)

---

## Submit a server

```bash
salad publish   # prompts for name/description/install, opens a pre-filled PR
```

Or open an [issue](../../issues/new?template=submit-server.yml) / send a PR by hand. See [CONTRIBUTING.md](CONTRIBUTING.md) for the YAML format.

---

## License

MIT
