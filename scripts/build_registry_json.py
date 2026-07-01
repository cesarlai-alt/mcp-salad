#!/usr/bin/env python3
"""Build website/registry.json from all server YAML files in registry/servers/.

Run this after adding or editing any server YAML to regenerate the JSON that
the website consumes:

    python3 scripts/build_registry_json.py
"""

import json
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parent
SERVERS_DIR = ROOT / "registry" / "servers"
OUTPUT = ROOT / "website" / "registry.json"


def build():
    if not SERVERS_DIR.exists():
        print(f"Error: registry directory not found: {SERVERS_DIR}", file=sys.stderr)
        sys.exit(1)

    servers = []
    for yaml_file in sorted(SERVERS_DIR.glob("*.yaml")):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
            if data:
                data["installable"] = True
                servers.append(data)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(servers, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(servers)} servers to {OUTPUT}")
    for s in servers:
        print(f"  - {s.get('name')}")


if __name__ == "__main__":
    build()
