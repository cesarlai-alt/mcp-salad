#!/usr/bin/env python3
"""
Generate registry/servers/*.yaml entries from the Vero gateway config.yaml.

Usage:
    python3 scripts/generate_from_config.py

Reads /Users/cesarlai/Documents/Claude/Vero/mcp-router/config.yaml,
skips entries already in registry/servers/, and writes new YAML files.
"""

import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parent
SERVERS_DIR = ROOT / "registry" / "servers"
CONFIG = Path("/Users/cesarlai/Documents/Claude/Vero/mcp-router/config.yaml")

# Private / Vero-internal capabilities to skip (not suitable for public registry)
SKIP_IDS = {
    "taiwan_stocks", "us_stocks", "web_research", "docs_library",
    "medical_research", "cipherlab_rag", "local_files", "maps_location",
}

# Capabilities already represented by existing registry files
ALREADY_IN_REGISTRY = {
    "notion",               # notion.yaml
    "alpha_vantage",        # alpha-vantage.yaml
    "yahoo_finance",        # yahoo-finance.yaml
    "context7",             # context7.yaml
    "pubmed",               # pubmed.yaml
    "google_maps",          # google-maps.yaml
    "obsidian_vero",        # obsidian-fs.yaml
    "twstock",              # twstock.yaml
    "clinicaltrials",       # will be covered by official entries
    "cipherlab_rag",        # private
    "salesforce",           # private
}

# Author guess from capability id prefix
def guess_author(cap_id: str) -> str:
    if cap_id.startswith("io_github_pipeworx"):
        return "pipeworx"
    if cap_id.startswith("io_github_cyanheads"):
        return "cyanheads"
    if cap_id.startswith("io_github_ojaskord"):
        return "ojaskord"
    if cap_id.startswith("eu_ansvar"):
        return "ansvar"
    if cap_id.startswith("dev_patent"):
        return "patent.dev"
    if cap_id.startswith("dev_cz_agents"):
        return "cz-agents"
    if cap_id.startswith("io_tooloracle"):
        return "tooloracle"
    if cap_id.startswith("ai_"):
        return "community"
    if cap_id.startswith("com_"):
        return "community"
    if cap_id.startswith("app_"):
        return "community"
    if cap_id.startswith("io_github_"):
        parts = cap_id.split("_")
        # io_github_<author>_...
        if len(parts) > 2:
            return parts[2]
    return "community"


def guess_homepage(cap_id: str, server_cfg: dict) -> str:
    if server_cfg.get("type") == "http":
        url = server_cfg.get("url", "")
        # strip path to get base domain
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"https://{parsed.netloc}"
    if cap_id.startswith("io_github_"):
        # io_github_<author>_<repo> -> github.com/author/repo-name
        parts = cap_id[len("io_github_"):].split("_")
        if len(parts) >= 2:
            author = parts[0]
            # guess repo name from remaining parts
            repo = "-".join(parts[1:])
            return f"https://github.com/{author}/{repo}"
    return ""


# Tag mapping by capability id keywords
TAG_MAP = [
    # Medical / Biomedical
    (["europepmc", "pubmed", "aria_mcp", "academic_research", "xpaysh"],
     ["medical", "research", "pubmed", "biomedical"]),
    (["clinicaltrials", "pharma_intel"],
     ["medical", "clinical-trials", "pharma", "research"]),
    (["openfda", "dailymed", "rxnorm", "icd_mcp", "medical_codes"],
     ["medical", "fda", "drugs", "clinical"]),
    (["pubchem", "chembl", "chemist"],
     ["chemistry", "drug-discovery", "biomedical"]),
    (["uniprot", "mygene", "ensembl"],
     ["genomics", "bioinformatics", "biomedical"]),

    # Finance / Economics
    (["edgar", "sec_xbrl", "edgartools"],
     ["finance", "sec", "filings", "stocks"]),
    (["fred", "imf", "oecd", "worldbank", "ecb", "bcb_br", "banxico"],
     ["economics", "central-bank", "macroeconomics", "finance"]),
    (["treasury_fiscaldata"],
     ["finance", "us-treasury", "economics"]),
    (["exchange_rates", "currencyguard"],
     ["finance", "forex", "currency"]),
    (["aws_pricing"],
     ["cloud", "aws", "pricing", "devops"]),

    # Trade / Customs
    (["comtrade", "trade_intel", "hts", "hs_code", "imf_portwatch"],
     ["trade", "customs", "international", "tariffs"]),
    (["shipping_rates"],
     ["shipping", "logistics", "ecommerce"]),
    (["open_corporates", "openregistry", "eu_registry"],
     ["company-registry", "kyb", "due-diligence"]),

    # Legal / Compliance
    (["taiwanese_law", "brazil_law", "japan_law"],
     ["legal", "legislation", "compliance"]),
    (["sanctions_screening", "vat_validator"],
     ["compliance", "legal", "kyc", "sanctions"]),

    # Patents / IP
    (["patent", "trademarks"],
     ["ip", "patents", "trademarks", "legal"]),

    # Research / Academic
    (["arxiv", "wolfram_alpha"],
     ["research", "academic", "science"]),

    # Productivity / Business
    (["linkedin"],
     ["crm", "sales", "linkedin", "outreach"]),
    (["neurodock", "translation"],
     ["productivity", "communication", "nlp"]),
    (["agent_tasks"],
     ["productivity", "task-management", "agents"]),
    (["meeting_summarizer"],
     ["productivity", "meetings", "ai"]),
    (["news_mcp", "agentic_news"],
     ["news", "monitoring", "intelligence"]),

    # Travel / Transport
    (["flights", "aviation", "hoteloracle"],
     ["travel", "aviation", "hotels"]),

    # Documents / Office
    (["docwand", "exactpdf"],
     ["pdf", "documents", "productivity"]),
    (["office_excel"],
     ["excel", "spreadsheets", "office"]),

    # Developer Tools
    (["docker_helper", "kubernetes"],
     ["devops", "docker", "kubernetes"]),
    (["regex_generator"],
     ["developer-tools", "utilities"]),

    # Media / Audio
    (["whisper", "macwhisper"],
     ["audio", "transcription", "speech-to-text"]),
    (["spotifyscraper"],
     ["music", "spotify", "media"]),
    (["podcasts"],
     ["podcasts", "media", "audio"]),

    # Geo / Maps
    (["openstreetmap"],
     ["maps", "geocoding", "geo"]),
    (["earthquake"],
     ["geoscience", "real-time", "alerts"]),
    (["weather"],
     ["weather", "real-time", "forecasting"]),

    # Utility / Data
    (["barcode_scanner"],
     ["barcode", "qr-code", "utilities"]),
    (["wikipedia"],
     ["reference", "knowledge", "wikipedia"]),
    (["vietnamese_calendar", "am_lich"],
     ["calendar", "localization", "vietnam"]),
    (["sqlite_mcp"],
     ["database", "sqlite", "sql"]),
    (["dynamic_feed"],
     ["data", "real-time", "utilities"]),
    (["yelp"],
     ["local-business", "reviews", "search"]),
    (["restcountries"],
     ["reference", "geography", "countries"]),
    (["macos_vision"],
     ["ocr", "vision", "macos", "pdf"]),
]


def guess_tags(cap_id: str) -> list:
    cap_lower = cap_id.lower()
    for keywords, tags in TAG_MAP:
        if any(kw in cap_lower for kw in keywords):
            return tags
    return ["utilities"]


def cap_id_to_filename(cap_id: str) -> str:
    return cap_id.replace("_", "-") + ".yaml"


def build_install(server_cfg: dict) -> dict:
    stype = server_cfg.get("type")
    if stype == "http":
        url = server_cfg.get("url", "")
        return {"type": "http", "url": url}
    else:
        install = {
            "type": "stdio",
            "command": server_cfg.get("command", ""),
            "args": server_cfg.get("args", []),
        }
        env = server_cfg.get("env", {})
        if env:
            env_required = []
            for key, val in env.items():
                env_required.append({"name": key, "description": f"Required environment variable"})
            install["env_required"] = env_required
        return install


def build_claude_config(server_cfg: dict) -> str:
    import json
    stype = server_cfg.get("type")
    if stype == "http":
        cfg = {"url": server_cfg.get("url", "")}
    else:
        cfg = {
            "command": server_cfg.get("command", ""),
            "args": server_cfg.get("args", []),
        }
        env = server_cfg.get("env", {})
        if env:
            env_out = {k: f"YOUR_{k}_HERE" for k in env}
            cfg["env"] = env_out
    return json.dumps(cfg, indent=2)


def display_name_from_id(cap_id: str) -> str:
    """Turn io_github_pipeworx_io_fred -> PipeworX FRED"""
    # Remove common prefixes
    name = cap_id
    for prefix in ["io_github_pipeworx_io_", "io_github_cyanheads_", "io_github_ojaskord_",
                   "io_github_", "com_", "app_", "ai_", "eu_ansvar_", "dev_"]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # Capitalize words
    parts = name.split("_")
    return " ".join(p.capitalize() for p in parts)


def main():
    with open(CONFIG) as f:
        config = yaml.safe_load(f)

    capabilities = config.get("capabilities", {})
    servers = config.get("servers", {})

    existing_files = {p.stem for p in SERVERS_DIR.glob("*.yaml")}
    print(f"Existing registry entries: {len(existing_files)}")

    created = []
    skipped = []

    for cap_id, cap_data in capabilities.items():
        if cap_id in SKIP_IDS:
            skipped.append((cap_id, "private/internal"))
            continue
        if cap_id in ALREADY_IN_REGISTRY:
            skipped.append((cap_id, "already in registry"))
            continue

        filename = cap_id_to_filename(cap_id)
        stem = filename[:-5]  # without .yaml

        if stem in existing_files:
            skipped.append((cap_id, "file already exists"))
            continue

        desc = cap_data.get("description", "")
        # Get server config
        cap_servers = cap_data.get("servers", [])
        server_cfg = {}
        if cap_servers:
            server_name = cap_servers[0]
            server_cfg = servers.get(server_name, {})

        if not server_cfg:
            # No server config found, still add as http entry with placeholder or skip
            skipped.append((cap_id, "no server config"))
            continue

        tags = guess_tags(cap_id)
        author = guess_author(cap_id)
        homepage = guess_homepage(cap_id, server_cfg)
        install = build_install(server_cfg)
        claude_config = build_claude_config(server_cfg)
        dname = display_name_from_id(cap_id)

        entry = {
            "name": stem,
            "display_name": dname,
            "description": desc,
            "author": author,
            "source": "official_registry",
            "tags": tags,
            "install": install,
            "claude_config": claude_config,
        }
        if homepage:
            entry["homepage"] = homepage

        out_path = SERVERS_DIR / filename
        with open(out_path, "w") as f:
            yaml.dump(entry, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        created.append(cap_id)
        print(f"  + {filename}")

    print(f"\nCreated: {len(created)} new registry entries")
    print(f"Skipped: {len(skipped)}")
    for s_id, reason in skipped:
        print(f"  - {s_id}: {reason}")

    return created


if __name__ == "__main__":
    main()
