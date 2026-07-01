#!/usr/bin/env bash
# Vero MCP Router — Install Script
# Usage: bash install.sh
# What it does:
#   1. Checks Python + pip dependencies
#   2. Adds router entry to ~/.mcp.json
#   3. Comments out (backs up) the local MCPs that router now manages
#   4. Prints test command

set -euo pipefail

ROUTER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROUTER_PY="$ROUTER_DIR/router.py"
MCP_JSON="$HOME/.mcp.json"
MCP_JSON_BACKUP="$HOME/.mcp.json.bak.$(date +%Y%m%d_%H%M%S)"

echo "=== Vero MCP Router Installer ==="
echo "Router path: $ROUTER_PY"
echo ""

# ── Step 1: Python dependencies ─────────────────────────────────────────────────
echo "[1/3] Checking Python dependencies..."
python3 -c "import mcp" 2>/dev/null || {
    echo "  Installing mcp SDK..."
    pip3 install -q mcp
}
python3 -c "import yaml" 2>/dev/null || {
    echo "  Installing PyYAML..."
    pip3 install -q PyYAML
}
echo "  ✓ Dependencies OK"
echo ""

# ── Step 2: Validate router.py syntax ───────────────────────────────────────────
echo "[2/3] Validating router.py..."
python3 -c "
import ast, sys
with open('$ROUTER_PY') as f:
    src = f.read()
try:
    ast.parse(src)
    print('  ✓ router.py syntax OK')
except SyntaxError as e:
    print(f'  ✗ Syntax error: {e}')
    sys.exit(1)
"

# ── Step 3: Update ~/.mcp.json ───────────────────────────────────────────────────
echo "[3/3] Updating ~/.mcp.json ..."

# Backup current config
cp "$MCP_JSON" "$MCP_JSON_BACKUP"
echo "  Backup saved to: $MCP_JSON_BACKUP"

# Read current config and add router entry
python3 << PYEOF
import json, sys

with open("$MCP_JSON") as f:
    config = json.load(f)

servers = config.setdefault("mcpServers", {})

# MCPs that router now manages — move to comment block (disable from direct registration)
ROUTER_MANAGED = ["twstock", "alpha_vantage", "alpha-vantage", "yahoo-finance",
                   "yahoo_finance", "firecrawl", "context7", "pubmed",
                   "clinicaltrials", "cipherlab-rag", "cipherlab_rag",
                   "salesforce", "obsidian_vero", "obsidian-vero",
                   "google-maps", "google_maps", "gdrive"]

moved = []
for key in list(servers.keys()):
    if key in ROUTER_MANAGED or key.replace("-", "_") in ROUTER_MANAGED:
        moved.append(key)
        # Don't delete — leave in place but log that router shadows them
        # (Claude Code won't see them once router is the only registered server for those)

# Add router entry
servers["vero-router"] = {
    "command": "python3",
    "args": ["$ROUTER_PY"]
}

with open("$MCP_JSON", "w") as f:
    json.dump(config, f, indent=4, ensure_ascii=False)

print(f"  ✓ Added vero-router to ~/.mcp.json")
if moved:
    print(f"  Note: The following servers are still in .mcp.json but router will handle them:")
    for m in moved:
        print(f"    - {m}")
    print()
    print("  OPTIONAL CLEANUP: If you want to remove direct registrations,")
    print("  edit ~/.mcp.json and remove those keys (keep the backup!).")
    print("  Only do this AFTER verifying the router works.")
PYEOF

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code (the router won't load until restart)"
echo "  2. Test with: python3 $ROUTER_PY"
echo "     (Ctrl+C to stop)"
echo ""
echo "  3. In Claude, try:"
echo "     use_capability('查台股收盤價')"
echo "     list_capabilities()"
echo ""
echo "  4. Check logs at: ~/.vero/logs/mcp-router.log"
echo ""
echo "IMPORTANT: The router is in ADDITIVE mode — existing MCPs still registered."
echo "Only remove them from ~/.mcp.json once you've verified the router works correctly."
