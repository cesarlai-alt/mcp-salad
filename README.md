# MCP Salad 🥗

**The MCP ecosystem has no package manager.**

500+ servers exist. They're scattered across GitHub repos, npm packages, and blog posts. There's no `npm install`. No search. No standard way to know what exists or how to try it.

MCP Salad fixes this: an open, community-maintained registry with a CLI and a searchable index. `mcp search web` finds what you need. `mcp install firecrawl` shows you exactly how to configure it.

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Servers](https://img.shields.io/badge/servers-8-brightgreen.svg)

## Quick Start

```bash
# Install CLI
pip install pyyaml click

# Search for servers
python3 cli/mcp.py search finance

# Get details
python3 cli/mcp.py info firecrawl

# Show install config
python3 cli/mcp.py install firecrawl

# Check your setup
python3 cli/mcp.py doctor
```

## Demo

```
$ python3 cli/mcp.py search weather

  Found 2 server(s) matching 'weather':

  open-meteo                 Free weather API — no key required...
  [weather] [api] [forecast]

  weather-gov                Official US National Weather Service data...
  [weather] [us] [forecast]
```

```
$ python3 cli/mcp.py install firecrawl

  Installing: Firecrawl

  Required environment variables:
    FIRECRAWL_API_KEY — Get from firecrawl.dev/app

  Add to Claude Desktop config:

  "firecrawl": {
    "command": "npx",
    "args": ["-y", "firecrawl-mcp"],
    "env": {"FIRECRAWL_API_KEY": "YOUR_KEY_HERE"}
  }

  ✓ Config snippet copied to clipboard!
```

## Browse Servers

| Name | Description | Tags |
|------|-------------|------|
| firecrawl | Web scraping and crawling | web, scraping |
| context7 | Live library documentation | docs, coding |
| pubmed | Biomedical literature search | research, health |
| yahoo-finance | Stock quotes and market data | finance, stocks |
| alpha-vantage | Comprehensive market data API | finance, forex |
| twstock | Taiwan stock market (TWSE/OTC) | finance, taiwan |
| google-maps | Location, directions, geocoding | maps, places |
| obsidian-fs | Read/write your Obsidian vault | notes, pkm |

## Website

The `website/` directory is a static, zero-build site (GitHub Pages compatible). It loads `registry.json` and renders a searchable card grid. Regenerate the JSON after adding servers:

```bash
python3 scripts/build_registry_json.py
```

Then serve locally to preview:

```bash
python3 -m http.server 8000 --directory website
# open http://localhost:8000
```

## Submit Your Server

Found an MCP server not in the registry? [Open an issue](../../issues/new?template=submit-server.yml) or submit a PR.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the YAML format.

## Companion Project

This registry pairs with [dynamic-mcp-gateway](https://github.com/cesarlai-alt/mcp-salad) — a router that lets Claude dynamically load MCP servers on demand without restarting.

## License

MIT
