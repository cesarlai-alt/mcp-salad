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

@click.group()
def cli():
    """MCP Registry — Find and install MCP servers for Claude.\n\nQuick start:\n  mcp search web\n  mcp install firecrawl\n  mcp list"""
    pass

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

@cli.command("list")
def list_servers():
    """List all available MCP servers."""
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

@cli.command()
@click.argument("name")
def info(name):
    """Show full details about an MCP server."""
    servers = get_servers()
    server = next((s for s in servers if s["name"] == name), None)
    if not server:
        click.echo(f"Server '{name}' not found. Run 'mcp list' to see available servers.")
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

@cli.command()
@click.argument("name")
def install(name):
    """Show install instructions for an MCP server."""
    servers = get_servers()
    server = next((s for s in servers if s["name"] == name), None)
    if not server:
        click.echo(f"Server '{name}' not found. Run 'mcp list' or 'mcp search <query>'.")
        sys.exit(1)

    click.echo(f"\n Installing: {click.style(server.get('display_name', name), fg='green', bold=True)}\n")

    install = server.get("install", {})
    install_type = install.get("type", "stdio")

    if install_type == "http":
        click.echo(f"  This is a Streamable HTTP server — no local installation needed!")
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

        # Try to copy to clipboard
        try:
            full_snippet = f'"{server["name"]}": {config_snippet}'
            if sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=full_snippet.encode(), check=True)
                click.echo(f"  ✓ Config snippet copied to clipboard!")
            elif sys.platform == "linux":
                subprocess.run(["xclip", "-selection", "clipboard"], input=full_snippet.encode(), check=True)
                click.echo(f"  ✓ Config snippet copied to clipboard!")
        except Exception:
            pass  # Clipboard copy is optional

@cli.command()
def doctor():
    """Check if common MCP dependencies are installed."""
    click.echo(f"\n MCP Doctor — checking dependencies\n")

    checks = [
        ("node", ["node", "--version"], "Node.js (required for npx-based servers)"),
        ("npx", ["npx", "--version"], "npx (runs MCP servers without global install)"),
        ("python3", ["python3", "--version"], "Python 3 (for Python-based MCP servers)"),
        ("uv", ["uv", "--version"], "uv (fast Python package runner, optional)"),
        ("git", ["git", "--version"], "Git (for source-based installs)"),
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
