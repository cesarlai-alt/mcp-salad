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

# Official MCP registry client (sibling module — script is run directly).
sys.path.insert(0, str(SCRIPT_DIR))
import official
LOCAL_REGISTRY = SCRIPT_DIR.parent / "registry" / "servers"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/your-org/mcp-registry/main/registry/servers"

# Gateway config — override via MCP_GATEWAY_CONFIG env var (used by tests)
import os as _os
import socket as _socket
GATEWAY_CONFIG = Path(_os.environ.get(
    "MCP_GATEWAY_CONFIG",
    str(SCRIPT_DIR.parent.parent / "mcp-router" / "config.yaml")
))

# Out-of-band control socket — must match router.CONTROL_SOCK_PATH.
# Override with MCP_SALAD_CONTROL_SOCK (tests point this at a temp path).
def _control_sock_path() -> Path:
    return Path(_os.environ.get(
        "MCP_SALAD_CONTROL_SOCK",
        str(Path.home() / ".mcp-salad" / "gateway.sock"),
    ))


# ── YAML helpers ──────────────────────────────────────────────────────────────

class _InlineListDumper(yaml.Dumper):
    """Dumper that keeps flat lists in flow style (e.g. args: ["-y", "npx"])."""
    pass


class _LiteralStr(str):
    """A string that should be dumped as a YAML literal block (|), like claude_config."""
    pass


def _represent_literal_str(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


_InlineListDumper.add_representer(_LiteralStr, _represent_literal_str)


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

def _search_local(query_lower):
    """Return curated local servers matching the query."""
    results = []
    for s in get_servers():
        searchable = " ".join([
            s.get("name", ""),
            s.get("display_name", ""),
            s.get("description", ""),
            " ".join(s.get("tags", []))
        ]).lower()
        if query_lower in searchable:
            results.append(s)
    return results


@cli.command()
@click.argument("query")
@click.option("--source", type=click.Choice(["local", "official", "all"]),
              default="all", show_default=True,
              help="Where to search: curated local registry, the official registry, or both.")
@click.option("--limit", type=int, default=20, show_default=True,
              help="Max official-registry results to show.")
def search(query, source, limit):
    """Search for MCP servers by keyword (local curated + official registry)."""
    query_lower = query.lower()

    local_results = _search_local(query_lower) if source in ("local", "all") else []
    local_names = {s["name"] for s in local_results}

    printed_any = False

    # ── Curated local hits first ──────────────────────────────────────────────
    if local_results:
        printed_any = True
        click.echo(f"\n Found {len(local_results)} curated server(s) matching '{query}':\n")
        for s in local_results:
            tag = click.style("[curated]", fg="magenta")
            click.echo(f"  {click.style(s['name'], fg='green', bold=True):<25} {tag} {s.get('description', '')[:55]}")
            tags = " ".join(f"[{t}]" for t in s.get("tags", [])[:4])
            if tags:
                click.echo(f"  {'':25} {click.style(tags, fg='cyan')}")
            click.echo()

    # ── Official-registry hits ────────────────────────────────────────────────
    if source in ("official", "all"):
        try:
            official_hits, total = official.search_servers(query, limit=limit)
        except official.OfficialRegistryError as exc:
            click.echo(f" Could not reach the official registry ({exc}). "
                       f"Showing local results only." if source == "all"
                       else f" Could not reach the official registry: {exc}")
            official_hits, total = [], 0

        # De-dup: skip official servers whose basename matches a curated hit.
        deduped = []
        for h in official_hits:
            basename = h["name"].split("/")[-1].replace("-", "_")
            if h["name"] in local_names or basename in {n.replace("-", "_") for n in local_names}:
                continue
            deduped.append(h)

        if deduped:
            printed_any = True
            click.echo(f"\n Found {total} server(s) in the official registry "
                       f"matching '{query}':\n")
            for h in deduped:
                tag = click.style("[official]", fg="blue")
                click.echo(f"  {click.style(h['name'], fg='green', bold=True):<32} {tag} {h.get('description', '')[:50]}")
                click.echo(f"  {'':32} {click.style(h['kind'] + ' · v' + (h.get('version') or '?'), fg='cyan')}")
                click.echo()
            if total > len(deduped):
                click.echo(f"  … and {total - len(deduped)} more in the official registry. "
                           f"Use --limit to see more.\n")

    if not printed_any:
        click.echo(f"No servers found for '{query}'")


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

def _parse_env_flags(env_vars) -> dict:
    provided = {}
    for ev in env_vars:
        if "=" in ev:
            k, v = ev.split("=", 1)
            provided[k] = v
    return provided


def _install_from_official(name, env_vars, yes):
    """Resolve a server from the official registry and add it to the gateway config."""
    # ── Resolve ───────────────────────────────────────────────────────────────
    try:
        item = official.get_server(name)
    except official.OfficialRegistryError as exc:
        click.echo(
            f"Server '{name}' isn't in the local registry, and the official "
            f"registry couldn't be reached ({exc})."
        )
        sys.exit(1)

    if item is None:
        click.echo(
            f"Server '{name}' not found in the local registry or the official "
            f"registry. Try 'mcp search {name}' to see close matches."
        )
        sys.exit(1)

    # ── Map to a gateway entry ────────────────────────────────────────────────
    try:
        entry, env_required = official.to_gateway_entry(item)
    except official.OfficialRegistryError as exc:
        click.echo(f"Can't install '{name}' from the official registry: {exc}")
        sys.exit(1)

    config = load_gateway_config()
    if config is None:
        click.echo(f"Gateway config not found at {GATEWAY_CONFIG}")
        sys.exit(1)
    config.setdefault("servers", {})
    config.setdefault("capabilities", {})

    server_id = official.official_server_id(name)
    if server_id in config["servers"]:
        click.echo(f"\n  '{server_id}' is already installed.")
        caps = get_capabilities_for_server(config, server_id)
        if caps:
            click.echo(f"  Capabilities: {', '.join(f'[{c}]' for c in caps)}")
        click.echo()
        return

    # ── Collect env vars (mirror the local-install UX) ────────────────────────
    provided_env = _parse_env_flags(env_vars)
    use_placeholders = yes or not sys.stdin.isatty()
    if env_required:
        click.echo(f"\n  This server requires configuration:")
        for env_item in env_required:
            var_name = env_item["name"]
            if var_name not in provided_env:
                click.echo(f"\n    {click.style(var_name, fg='yellow')}: {env_item.get('description', '')}")
                if use_placeholders:
                    click.echo(f"    Using placeholder: ${{{var_name}}}")
                else:
                    value = click.prompt(
                        f"    Value for {var_name} (Enter to use placeholder)",
                        default="", show_default=False,
                    )
                    if value:
                        provided_env[var_name] = value

        # Fold real values into whichever placeholder block to_gateway_entry made.
        for block in ("env", "headers"):
            if block in entry:
                for var_name in list(entry[block].keys()):
                    if provided_env.get(var_name):
                        entry[block][var_name] = provided_env[var_name]

    config["servers"][server_id] = entry

    # ── Assign a capability (official servers carry no tags → new capability) ──
    srv = item.get("server", {})
    description = srv.get("description") or srv.get("title") or name
    keywords = [t for t in official.official_server_id(name).split("_") if len(t) > 1]
    config["capabilities"][server_id] = {
        "description": description,
        "keywords": keywords or [server_id],
        "servers": [server_id],
    }

    save_gateway_config(config)

    kind = "HTTP remote" if entry.get("type") == "http" else f"stdio ({entry.get('command')})"
    click.echo(f"\n  ✅ {server_id} installed from the official registry ({kind}).")
    click.echo(f"  Capability: [{server_id}]")
    click.echo(f"  Call `use_capability('{server_id}')` to activate.\n")


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
        # Not curated locally — try resolving from the official registry.
        _install_from_official(name, env_vars, yes)
        return

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


# ── Enable / Disable (out-of-band, live gateway) ──────────────────────────────

def _send_control_command(command: str, sock_path: Path, timeout: float = 5.0) -> str:
    """Connect to the gateway control socket, send one line, return the reply."""
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(sock_path))
        sock.sendall((command + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode("utf-8", errors="replace").strip()
    finally:
        sock.close()


def _run_control_command(action: str, server: str) -> None:
    """Shared impl for `enable`/`disable`: talk to the live gateway, print reply."""
    sock_path = _control_sock_path()
    friendly = (
        f"Gateway isn't running (no control socket at {sock_path}). "
        f"Start it and try again."
    )
    if not sock_path.exists():
        click.echo(friendly)
        sys.exit(1)
    try:
        reply = _send_control_command(f"{action} {server}", sock_path)
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        # Socket file is stale or no listener — same friendly message, no traceback.
        click.echo(friendly)
        sys.exit(1)

    click.echo(reply)
    sys.exit(0 if reply.startswith("ok") else 1)


@cli.command()
@click.argument("server")
def enable(server):
    """Enable a server in a RUNNING Gateway (no restart) — live tool injection."""
    _run_control_command("enable", server)


@cli.command()
@click.argument("server")
def disable(server):
    """Disable a server in a RUNNING Gateway (no restart)."""
    _run_control_command("disable", server)


# ── Doctor ────────────────────────────────────────────────────────────────────

def _check_http(url: str, timeout: float = 2.0) -> tuple:
    """Return (reachable: bool, message: str) for an HTTP server URL."""
    try:
        import httpx
        try:
            with httpx.Client(timeout=timeout, verify=False) as client:
                resp = client.get(url)
            reachable = 200 <= resp.status_code < 500
            return reachable, f"HTTP {resp.status_code}"
        except Exception as exc:
            return False, str(exc)
    except ImportError:
        pass

    # Fall back to urllib.request
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # 4xx responses still mean the server is reachable
        reachable = 400 <= exc.code < 500
        return reachable, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


@cli.command()
def doctor():
    """Check health of installed MCP servers in the Gateway config."""
    import shutil as _shutil

    config = load_gateway_config()

    click.echo(f"\n MCP Salad Doctor 🥗\n")
    click.echo(f" Gateway config: {GATEWAY_CONFIG}")

    if config is None:
        click.echo(f" Gateway config not found at {GATEWAY_CONFIG}")
        sys.exit(1)

    installed = config.get("servers") or {}
    click.echo(f" Installed servers: {len(installed)}\n")

    if not installed:
        click.echo("  No servers installed. Run `mcp install <name>` to get started.")
        sys.exit(0)

    # Build a name→registry-entry lookup for env_required checks
    registry_by_id: dict = {}
    for s in load_local_registry():
        reg_name = s.get("name", "")
        # Store under both the raw name and the normalized (underscore) form
        registry_by_id[reg_name] = s
        registry_by_id[normalize_server_id(reg_name)] = s

    issues = 0

    for server_id, server_def in installed.items():
        server_type = server_def.get("type", "stdio")
        caps = get_capabilities_for_server(config, server_id)
        cap_label = caps[0] if caps else "no_capability"

        if server_type == "http":
            url = server_def.get("url", "")
            ok, detail = _check_http(url)
            status = click.style("✅", fg="green") if ok else click.style("❌", fg="red")
            msg = f"reachable (HTTP)" if ok else f"connection failed: {detail}"
            if not ok:
                issues += 1
        else:
            command = server_def.get("command", "")
            found = _shutil.which(command)
            ok = found is not None
            status = click.style("✅", fg="green") if ok else click.style("❌", fg="red")
            msg = f"command found: {command}" if ok else f"command not found: {command}"
            if not ok:
                issues += 1

        click.echo(f"  {server_id:<16} [{cap_label}]   {status} {msg}")

        # Env-var check: cross-reference registry for env_required
        reg_entry = registry_by_id.get(server_id)
        if reg_entry:
            env_required = reg_entry.get("install", {}).get("env_required", [])
            server_env = server_def.get("env", {})
            for env_item in env_required:
                var_name = env_item["name"]
                configured_val = server_env.get(var_name, "")
                is_placeholder = (
                    isinstance(configured_val, str)
                    and configured_val.startswith("${")
                    and configured_val.endswith("}")
                )
                in_real_env = var_name in os.environ
                if is_placeholder and not in_real_env:
                    click.echo(f"    {click.style('⚠️', fg='yellow')}  {var_name} is still a placeholder — set it before use")
                    issues += 1

    click.echo()
    if issues == 0:
        click.echo(f" {click.style('All servers healthy.', fg='green', bold=True)}\n")
    else:
        click.echo(f" {click.style(str(issues) + ' issue(s) found.', fg='yellow')}\n")

    sys.exit(0 if issues == 0 else 1)


# ── Upgrade ───────────────────────────────────────────────────────────────────

def _build_canonical_entry(server: dict, existing_entry: dict = None) -> dict:
    """Build the 'should-be' server entry from registry data.

    Preserves existing env values so user credentials are never overwritten.
    """
    install_section = server.get("install", {})
    install_type = install_section.get("type", "stdio")
    env_req = install_section.get("env_required", [])

    if install_type == "http":
        return {
            "type": "http",
            "url": install_section.get("url", ""),
        }

    entry: dict = {
        "type": "stdio",
        "command": install_section.get("command", "npx"),
        "args": list(install_section.get("args", [])),
    }
    if env_req:
        existing_env = (existing_entry or {}).get("env", {})
        env_dict = {}
        for env_item in env_req:
            var_name = env_item["name"]
            env_dict[var_name] = existing_env.get(var_name, f"${{{var_name}}}")
        entry["env"] = env_dict
    return entry


def _entry_summary(entry: dict) -> str:
    """One-line display string for diff output."""
    if entry.get("type") == "http":
        return entry.get("url", "")
    cmd = entry.get("command", "")
    args = " ".join(entry.get("args", []))
    return f"{cmd} {args}".strip()


def _entries_differ(current: dict, new: dict) -> bool:
    """True if structural config differs (env *values* are ignored, only key-set is checked)."""
    for key in ("type", "command", "args", "url"):
        if current.get(key) != new.get(key):
            return True
    current_env_keys = set((current.get("env") or {}).keys())
    new_env_keys = set((new.get("env") or {}).keys())
    return current_env_keys != new_env_keys


def _upgrade_one(server_id: str, config: dict, registry_servers: list) -> bool:
    """Upgrade a single server in-place. Returns True if config was changed."""
    click.echo(f"\nUpgrading {server_id}...")

    installed = config.get("servers") or {}
    if server_id not in installed:
        click.echo(f"  ⚠️  '{server_id}' is not installed — skipping.")
        return False

    reg_server = next(
        (s for s in registry_servers if normalize_server_id(s["name"]) == server_id),
        None,
    )
    if reg_server is None:
        click.echo(f"  ⚠️  '{server_id}' not found in registry — cannot upgrade.")
        return False

    current_entry = installed[server_id]
    new_entry = _build_canonical_entry(reg_server, existing_entry=current_entry)

    if not _entries_differ(current_entry, new_entry):
        click.echo("  No changes — already up to date.")
        return False

    current_summary = _entry_summary(current_entry)
    new_summary = _entry_summary(new_entry)
    if current_summary != new_summary:
        click.echo(f"  Current:  {current_summary}")
        click.echo(f"  Registry: {new_summary}")
        click.echo("  ↑ args updated.")

    config["servers"][server_id] = new_entry
    click.echo(f"\n  ✅ {server_id} upgraded.")
    return True


@cli.command()
@click.argument("name", required=False)
@click.option("--all", "upgrade_all", is_flag=True, default=False,
              help="Upgrade all installed servers")
def upgrade(name, upgrade_all):
    """Upgrade an installed server's config entry from the registry."""
    if not name and not upgrade_all:
        click.echo("Usage: mcp upgrade <name>  OR  mcp upgrade --all")
        sys.exit(1)

    config = load_gateway_config()
    if config is None:
        click.echo(f"Gateway config not found at {GATEWAY_CONFIG}")
        sys.exit(1)

    config.setdefault("servers", {})
    registry_servers = get_servers()

    if upgrade_all:
        installed_ids = list(config["servers"].keys())
        if not installed_ids:
            click.echo("\n  No servers installed to upgrade.\n")
            return
        changed = sum(
            1 for sid in installed_ids if _upgrade_one(sid, config, registry_servers)
        )
        if changed:
            save_gateway_config(config)
        click.echo(f"\n  {changed} server(s) upgraded.\n")
    else:
        server_id = normalize_server_id(name)
        changed = _upgrade_one(server_id, config, registry_servers)
        if changed:
            save_gateway_config(config)
        click.echo()


# ── Publish ───────────────────────────────────────────────────────────────────

import re
from urllib.parse import quote

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _split_multi(value: str) -> list:
    """Split a comma- or whitespace-separated string into a clean list."""
    if not value:
        return []
    parts = re.split(r"[,\s]+", value.strip())
    return [p for p in parts if p]


def _build_publish_entry(name, display_name, description, author, homepage,
                         license_, tags, command, args, env_required) -> dict:
    """Assemble a registry entry dict matching the firecrawl.yaml schema exactly."""
    install: dict = {
        "type": "stdio",
        "command": command,
        "args": list(args),
    }
    if env_required:
        install["env_required"] = env_required

    # Build the claude_config JSON block from command/args/env.
    cc: dict = {"command": command, "args": list(args)}
    if env_required:
        cc["env"] = {e["name"]: "YOUR_KEY_HERE" for e in env_required}
    claude_config = json.dumps(cc, indent=2) + "\n"

    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "author": author,
        "homepage": homepage,
        "license": license_,
        "tags": list(tags),
        "install": install,
        "claude_config": _LiteralStr(claude_config),
    }


@cli.command()
@click.option("--name", help="Server name (kebab-case: lowercase, digits, hyphens)")
@click.option("--display-name", help="Human-friendly display name")
@click.option("--description", help="One-line description")
@click.option("--author", help="GitHub handle or org")
@click.option("--homepage", help="Project homepage URL")
@click.option("--license", "license_", default="MIT", show_default=True, help="License")
@click.option("--tags", help="Comma-separated tags")
@click.option("--command", help="Install command (e.g. npx, python3, node)")
@click.option("--args", "args_str", help="Install args (space or comma separated)")
@click.option("--out-dir", type=click.Path(), default=None,
              help="Directory to write the YAML into (defaults to the local registry)")
@click.option("--force", is_flag=True, default=False, help="Overwrite an existing entry")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Non-interactive: use flags/defaults without prompting")
def publish(name, display_name, description, author, homepage, license_, tags,
            command, args_str, out_dir, force, yes):
    """Publish a new server: generate a registry YAML + a GitHub submission URL."""
    # ── Decide interactive vs non-interactive ─────────────────────────────────
    interactive = sys.stdin.isatty() and not name and not yes

    env_required: list = []

    if interactive:
        click.echo(f"\n 🥗 Publish a new MCP server to the registry\n")
        name = click.prompt(" Server name (kebab-case)").strip()
        if not _KEBAB_RE.match(name):
            click.echo(f"\n ❌ Invalid name '{name}'. Use lowercase letters, digits and hyphens only.", err=True)
            sys.exit(1)
        display_name = click.prompt(" Display name", default=name.replace("-", " ").title()).strip()
        description = click.prompt(" Description (one line)").strip()
        author = click.prompt(" Author (github handle or org)").strip()
        homepage = click.prompt(" Homepage URL", default="").strip()
        license_ = click.prompt(" License", default="MIT").strip()
        tags = _split_multi(click.prompt(" Tags (comma-separated)", default="").strip())
        command = click.prompt(" Install command (e.g. npx, python3, node)", default="npx").strip()
        args = _split_multi(click.prompt(" Install args (space or comma separated)", default="").strip())

        # Loop env vars — empty name ends the loop
        click.echo("\n Required env vars (press Enter on an empty name to finish):")
        while True:
            ev_name = click.prompt("   env var name", default="", show_default=False).strip()
            if not ev_name:
                break
            ev_desc = click.prompt(f"   description for {ev_name}", default="").strip()
            ev_url = click.prompt(f"   url to obtain {ev_name}", default="").strip()
            item = {"name": ev_name, "description": ev_desc}
            if ev_url:
                item["url"] = ev_url
            env_required.append(item)
    else:
        # Non-interactive: name is required.
        if not name:
            click.echo(
                " ❌ Error: --name is required in non-interactive mode.\n"
                "    Run in a terminal for interactive prompts, or pass --name (and other flags).",
                err=True,
            )
            sys.exit(1)
        display_name = display_name or name.replace("-", " ").title()
        description = description or ""
        author = author or ""
        homepage = homepage or ""
        tags = _split_multi(tags) if tags else []
        command = command or "npx"
        args = _split_multi(args_str) if args_str else []

    # ── Validate name ─────────────────────────────────────────────────────────
    if not _KEBAB_RE.match(name):
        click.echo(
            f" ❌ Invalid name '{name}'. Server names must be kebab-case "
            "(lowercase letters, digits and hyphens only).",
            err=True,
        )
        sys.exit(1)

    # ── Assemble entry ────────────────────────────────────────────────────────
    entry = _build_publish_entry(
        name, display_name, description, author, homepage,
        license_, tags, command, args, env_required,
    )
    yaml_text = _yaml_dump(entry)

    # ── Resolve output path ───────────────────────────────────────────────────
    out_directory = Path(out_dir) if out_dir else LOCAL_REGISTRY
    out_directory.mkdir(parents=True, exist_ok=True)
    out_path = out_directory / f"{name}.yaml"

    if out_path.exists() and not force:
        click.echo(
            f"\n ⚠️  {out_path} already exists. Use --force to overwrite.\n",
            err=True,
        )
        sys.exit(1)

    out_path.write_text(yaml_text)

    # ── Generate GitHub submission URL ────────────────────────────────────────
    issue_title = f"Add server: {name}"
    issue_body = (
        f"Proposed registry entry for `{name}`.\n\n"
        f"```yaml\n{yaml_text}```\n"
    )
    github_url = (
        "https://github.com/cesarlai-alt/mcp-salad/issues/new"
        f"?title={quote(issue_title)}&body={quote(issue_body)}"
    )

    # ── Success summary ───────────────────────────────────────────────────────
    click.echo(f"\n ✅ Wrote registry entry: {out_path}")
    click.echo(f"\n Submit it to the registry by opening this GitHub issue URL:\n")
    click.echo(f"   {github_url}\n")
    click.echo(" Opening the link (and submitting the pre-filled issue) completes your submission. 🥗\n")


if __name__ == "__main__":
    cli()
