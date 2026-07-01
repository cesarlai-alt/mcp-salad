#!/usr/bin/env python3
"""MCP Registry CLI — find and install MCP servers."""

import click
import yaml
import json
import os
import sys
import subprocess
from pathlib import Path

# Auto-detect registry location: sibling ../registry/servers/ OR fallback to GitHub raw
SCRIPT_DIR = Path(__file__).parent
LOCAL_REGISTRY = SCRIPT_DIR.parent / "registry" / "servers"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/your-org/mcp-registry/main/registry/servers"

# Gateway config — override via MCP_GATEWAY_CONFIG env var (used by tests)
import os as _os
GATEWAY_CONFIG = Path(_os.environ.get(
    "MCP_GATEWAY_CONFIG",
    str(SCRIPT_DIR.parent.parent / "mcp-router" / "config.yaml")
))


# ── YAML helpers ──────────────────────────────────────────────────────────────

class _InlineListDumper(yaml.Dumper):
    """Dumper that keeps flat lists in flow style (e.g. args: ["-y", "npx"])."""
    pass


def _represent_list(dumper, data):
    # Flow style for lists whose items are all scalars (str/int/float/bool)
    if all(isinstance(item, (str, int, float, bool)) for item in data):
        return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


_InlineListDumper.add_representer(list, _represent_list)


def _yaml_dump(config) -> str:
    return yaml.dump(
        config,
        Dumper=_InlineListDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


# ── Registry helpers ──────────────────────────────────────────────────────────

def load_local_registry():
    """Load all server YAMLs from local registry directory."""
    servers = []
    if LOCAL_REGISTRY.exists():
        for yaml_file in sorted(LOCAL_REGISTRY.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data:
                    servers.append(data)
    return servers


def get_servers():
    servers = load_local_registry()
    if not servers:
        click.echo("Warning: No local registry found. Run from the mcp-registry directory.", err=True)
    return servers


def normalize_server_id(name: str) -> str:
    """Normalize a registry server name to a config.yaml key (hyphens → underscores)."""
    return name.replace("-", "_")


# ── Gateway config helpers ────────────────────────────────────────────────────

def load_gateway_config():
    """Load the gateway config.yaml. Returns None if missing."""
    if not GATEWAY_CONFIG.exists():
        return None
    with open(GATEWAY_CONFIG) as f:
        return yaml.safe_load(f)


def save_gateway_config(config: dict) -> None:
    """Write gateway config.yaml (comments are not preserved; pyyaml limitation)."""
    header = (
        "# Vero MCP Router — Capability Configuration\n"
        "# Managed by mcp-cli — edit with care\n"
        "#\n"
        "# Each capability maps a natural-language description to one or more local MCP servers.\n"
        "# keywords: matched against use_capability() input (case-insensitive, partial match)\n\n"
    )
    with open(GATEWAY_CONFIG, "w") as f:
        f.write(header)
        f.write(_yaml_dump(config))


def get_capabilities_for_server(config: dict, server_id: str) -> list:
    """Return list of capability names that include this server_id."""
    caps = []
    for cap_name, cap_data in (config.get("capabilities") or {}).items():
        if server_id in (cap_data.get("servers") or []):
            caps.append(cap_name)
    return caps


# ── CLI entry point ───────────────────────────────────────────────────────────

@click.group()
def cli():
    """MCP Registry — Find and install MCP servers for Claude.\n
Quick start:\n
  mcp search web\n
  mcp install firecrawl\n
  mcp list"""
    pass


# ── Search ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("query")
def search(query):
    """Search for MCP servers by keyword."""
    servers = get_servers()
    query_lower = query.lower()
    results = []
    for s in servers:
        searchable = " ".join([
            s.get("name", ""),
            s.get("display_name", ""),
            s.get("description", ""),
            " ".join(s.get("tags", []))
        ]).lower()
        if query_lower in searchable:
            results.append(s)

    if not results:
        click.echo(f"No servers found for '{query}'")
        return

    click.echo(f"\n Found {len(results)} server(s) matching '{query}':\n")
    for s in results:
        tags = " ".join(f"[{t}]" for t in s.get("tags", [])[:4])
        click.echo(f"  {click.style(s['name'], fg='green', bold=True):<25} {s.get('description', '')[:60]}...")
        if tags:
            click.echo(f"  {'':25} {click.style(tags, fg='cyan')}")
        click.echo()


# ── Registry (was: list) ──────────────────────────────────────────────────────

@cli.command("registry")
def list_registry():
    """List all MCP servers available in the registry."""
    servers = get_servers()
    click.echo(f"\n MCP Registry — {len(servers)} servers available\n")
    click.echo(f"  {'NAME':<25} {'DESCRIPTION':<55} {'TAGS'}")
    click.echo(f"  {'─'*25} {'─'*55} {'─'*30}")
    for s in servers:
        tags = ", ".join(s.get("tags", [])[:3])
        desc = s.get("description", "")[:54]
        name = click.style(s["name"], fg="green")
        click.echo(f"  {name:<34} {desc:<55} {click.style(tags, fg='cyan')}")
    click.echo()


# ── List (installed) ──────────────────────────────────────────────────────────

@cli.command("list")
def list_installed():
    """List MCP servers currently installed in the Gateway."""
    config = load_gateway_config()
    if config is None:
        click.echo(f"Gateway config not found at {GATEWAY_CONFIG}")
        click.echo("Make sure mcp-router is set up correctly.")
        return

    installed = config.get("servers") or {}
    if not installed:
        click.echo("\n No servers installed yet. Run 'mcp install <name>' to add one.\n")
        return

    click.echo(f"\n Installed servers ({len(installed)}):\n")
    for server_id, _ in installed.items():
        caps = get_capabilities_for_server(config, server_id)
        caps_str = "  ".join(f"[{c}]" for c in caps) if caps else "[no capability]"
        status = click.style("inactive", fg="yellow")
        click.echo(f"  {click.style(server_id, fg='green'):<22} {caps_str:<35} {status}")
    click.echo()


# ── Info ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("name")
def info(name):
    """Show full details about an MCP server."""
    servers = get_servers()
    server = next((s for s in servers if s["name"] == name), None)
    if not server:
        click.echo(f"Server '{name}' not found. Run 'mcp registry' to see all servers.")
        sys.exit(1)

    click.echo(f"\n {click.style(server.get('display_name', server['name']), fg='green', bold=True)}")
    click.echo(f"  {server.get('description', '')}\n")
    click.echo(f"  Author:   {server.get('author', 'unknown')}")
    click.echo(f"  Homepage: {server.get('homepage', 'N/A')}")
    click.echo(f"  License:  {server.get('license', 'unknown')}")
    click.echo(f"  Tags:     {', '.join(server.get('tags', []))}")

    install = server.get("install", {})
    click.echo(f"\n  Install type: {install.get('type', 'stdio')}")

    env_req = install.get("env_required", [])
    if env_req:
        click.echo(f"\n  Required environment variables:")
        for env in env_req:
            click.echo(f"    • {env['name']}: {env['description']}")
            if "url" in env:
                click.echo(f"      Get it at: {env['url']}")

    if "claude_config" in server:
        click.echo(f"\n  Claude Desktop config snippet:")
        click.echo(f"  Add to ~/Library/Application Support/Claude/claude_desktop_config.json:")
        click.echo(f"\n  \"{server['name']}\": {server['claude_config'].strip()}")
    click.echo()


# ── Show-config (was: install) ────────────────────────────────────────────────

@cli.command("show-config")
@click.argument("name")
def show_config(name):
    """Show Claude Desktop config snippet for an MCP server."""
    servers = get_servers()
    server = next((s for s in servers if s["name"] == name), None)
    if not server:
        click.echo(f"Server '{name}' not found. Run 'mcp registry' or 'mcp search <query>'.")
        sys.exit(1)

    click.echo(f"\n Config snippet: {click.style(server.get('display_name', name), fg='green', bold=True)}\n")

    install = server.get("install", {})
    install_type = install.get("type", "stdio")

    if install_type == "http":
        click.echo(f"  Streamable HTTP server — no local installation needed!")
        click.echo(f"  URL: {install.get('url', '')}\n")
    else:
        cmd = install.get("command", "npx")
        args = " ".join(install.get("args", []))
        click.echo(f"  Run manually: {cmd} {args}\n")

    env_req = install.get("env_required", [])
    if env_req:
        click.echo(f"  Required environment variables:")
        for env in env_req:
            click.echo(f"    {env['name']} — {env['description']}")
        click.echo()

    if "claude_config" in server:
        config_snippet = server["claude_config"].strip()
        click.echo(f"  Add to Claude Desktop config (~/.../claude_desktop_config.json):")
        click.echo(f"\n  \"{server['name']}\": {config_snippet}\n")

        try:
            full_snippet = f'"{server["name"]}": {config_snippet}'
            if sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=full_snippet.encode(), check=True)
                click.echo(f"  ✓ Config snippet copied to clipboard!")
            elif sys.platform == "linux":
                subprocess.run(["xclip", "-selection", "clipboard"], input=full_snippet.encode(), check=True)
                click.echo(f"  ✓ Config snippet copied to clipboard!")
        except Exception:
            pass


# ── Install ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("name")
@click.option("--env", "env_vars", multiple=True, metavar="KEY=VALUE",
              help="Provide an env var (repeatable): --env FIRECRAWL_API_KEY=abc123")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Use placeholder values for required env vars without prompting")
def install(name, env_vars, yes):
    """Install an MCP server into the Gateway config."""
    # ── 1. Find server in registry ────────────────────────────────────────────
    servers = get_servers()
    server = next((s for s in servers if s["name"] == name), None)
    if not server:
        click.echo(f"Server '{name}' not found. Try 'mcp registry' or 'mcp search <query>'.")
        sys.exit(1)

    # ── 2. Load gateway config ────────────────────────────────────────────────
    config = load_gateway_config()
    if config is None:
        click.echo(f"Gateway config not found at {GATEWAY_CONFIG}")
        sys.exit(1)

    config.setdefault("servers", {})
    config.setdefault("capabilities", {})

    server_id = normalize_server_id(name)

    # ── 3. Already installed? ─────────────────────────────────────────────────
    if server_id in config["servers"]:
        click.echo(f"\n  '{server_id}' is already installed.")
        caps = get_capabilities_for_server(config, server_id)
        if caps:
            click.echo(f"  Capabilities: {', '.join(f'[{c}]' for c in caps)}")
        click.echo()
        return

    # ── 4. Collect env vars ───────────────────────────────────────────────────
    install_section = server.get("install", {})
    install_type = install_section.get("type", "stdio")
    env_req = install_section.get("env_required", [])

    # Parse --env flags
    provided_env: dict = {}
    for ev in env_vars:
        if "=" in ev:
            k, v = ev.split("=", 1)
            provided_env[k] = v

    # Interactive prompt for any missing required vars
    use_placeholders = yes or not sys.stdin.isatty()
    if env_req:
        click.echo(f"\n  This server requires environment variables:")
        for env_item in env_req:
            var_name = env_item["name"]
            if var_name not in provided_env:
                click.echo(f"\n    {click.style(var_name, fg='yellow')}: {env_item['description']}")
                if "url" in env_item:
                    click.echo(f"    Get it at: {env_item['url']}")
                if use_placeholders:
                    click.echo(f"    Using placeholder: ${{{var_name}}}")
                else:
                    value = click.prompt(
                        f"    Value for {var_name} (Enter to use placeholder)",
                        default="",
                        show_default=False,
                    )
                    if value:
                        provided_env[var_name] = value

    # ── 5. Build server entry ─────────────────────────────────────────────────
    if install_type == "http":
        server_entry: dict = {
            "type": "http",
            "url": install_section.get("url", ""),
        }
    else:
        server_entry = {
            "type": "stdio",
            "command": install_section.get("command", "npx"),
            "args": list(install_section.get("args", [])),
        }
        if env_req:
            env_dict = {}
            for env_item in env_req:
                var_name = env_item["name"]
                env_dict[var_name] = (
                    provided_env[var_name]
                    if provided_env.get(var_name)
                    else f"${{{var_name}}}"
                )
            server_entry["env"] = env_dict

    config["servers"][server_id] = server_entry

    # ── 6. Assign to a capability ─────────────────────────────────────────────
    # Already covered by an existing capability?
    existing_caps = get_capabilities_for_server(config, server_id)
    if existing_caps:
        cap_used = existing_caps[0]
    else:
        # Try to match by tag overlap with existing capability descriptions/names
        tags = [t.lower() for t in server.get("tags", [])]
        matched_cap = None
        for cap_name, cap_data in config["capabilities"].items():
            cap_text = (cap_data.get("description", "") + " " + cap_name).lower()
            if any(tag in cap_text for tag in tags):
                matched_cap = cap_name
                break

        if matched_cap:
            cap_servers = config["capabilities"][matched_cap].setdefault("servers", [])
            cap_servers.append(server_id)
            cap_used = matched_cap
        else:
            # Create new capability named after this server
            cap_name = server_id  # already underscore-normalized
            config["capabilities"][cap_name] = {
                "description": server.get("description", name),
                "keywords": list(server.get("tags", [name])),
                "servers": [server_id],
            }
            cap_used = cap_name

    # ── 7. Persist ────────────────────────────────────────────────────────────
    save_gateway_config(config)

    click.echo(f"\n  ✅ {server_id} installed.")
    click.echo(f"  Capability: [{cap_used}]")
    click.echo(f"  Call `use_capability('{cap_used}')` to activate.\n")


# ── Uninstall ─────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("name")
def uninstall(name):
    """Remove an MCP server from the Gateway config."""
    config = load_gateway_config()
    if config is None:
        click.echo(f"Gateway config not found at {GATEWAY_CONFIG}")
        sys.exit(1)

    server_id = normalize_server_id(name)
    installed = config.get("servers") or {}

    if server_id not in installed:
        click.echo(f"\n  '{server_id}' is not installed.\n")
        return

    # Remove from servers section
    del config["servers"][server_id]

    # Remove from any capabilities that reference it
    removed_from: list = []
    for cap_name, cap_data in (config.get("capabilities") or {}).items():
        cap_servers = cap_data.get("servers") or []
        if server_id in cap_servers:
            cap_servers.remove(server_id)
            cap_data["servers"] = cap_servers
            removed_from.append(cap_name)

    save_gateway_config(config)

    click.echo(f"\n  Removed: {server_id}")
    if removed_from:
        click.echo(f"  Removed from capabilities: {', '.join(removed_from)}")
    click.echo()


# ── Doctor ────────────────────────────────────────────────────────────────────

@cli.command()
def doctor():
    """Check if common MCP dependencies are installed."""
    click.echo(f"\n MCP Doctor — checking dependencies\n")

    checks = [
        ("node",    ["node", "--version"],    "Node.js (required for npx-based servers)"),
        ("npx",     ["npx", "--version"],     "npx (runs MCP servers without global install)"),
        ("python3", ["python3", "--version"], "Python 3 (for Python-based MCP servers)"),
        ("uv",      ["uv", "--version"],      "uv (fast Python package runner, optional)"),
        ("git",     ["git", "--version"],     "Git (for source-based installs)"),
    ]

    all_ok = True
    for cmd, test_cmd, label in checks:
        try:
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=5)
            version = result.stdout.strip().split("\n")[0]
            click.echo(f"  {click.style('✓', fg='green')} {label}")
            click.echo(f"    {version}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            click.echo(f"  {click.style('✗', fg='red')} {label}")
            click.echo(f"    Not found — some servers may not work")
            all_ok = False
        click.echo()

    if all_ok:
        click.echo(f"  {click.style('All dependencies found!', fg='green', bold=True)}\n")
    else:
        click.echo(f"  {click.style('Some dependencies missing.', fg='yellow')} Install them for full compatibility.\n")


if __name__ == "__main__":
    cli()
