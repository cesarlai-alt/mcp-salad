#!/usr/bin/env python3
"""Client for the official MCP registry (registry.modelcontextprotocol.io).

HTTP is done with `curl` via subprocess on purpose: macOS system Python has
SSL cert issues with urllib, and the curl seam is trivial to stub in tests.

Testing seams (no network needed):
  * MCP_OFFICIAL_FIXTURE = path to a canned `/v0/servers` JSON page. When set,
    fetch_page() reads that file instead of hitting the network and filters it
    client-side by the `search` term.
  * MCP_OFFICIAL_FAIL = "1" makes every fetch raise OfficialRegistryError, to
    exercise the graceful-failure path.
"""

import json
import os
import subprocess
from urllib.parse import urlencode

OFFICIAL_BASE = "https://registry.modelcontextprotocol.io"
_META_KEY = "io.modelcontextprotocol.registry/official"


class OfficialRegistryError(Exception):
    """Raised when the official registry can't be reached or returns garbage."""


# ── Low-level fetch (the monkeypatch/stub seam) ───────────────────────────────

def _curl_json(url: str, timeout: float = 15.0) -> dict:
    """GET a URL with curl and parse the JSON body. Raises OfficialRegistryError."""
    try:
        proc = subprocess.run(
            ["curl", "-s", "--max-time", str(int(timeout)), url],
            capture_output=True,
            timeout=timeout + 5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise OfficialRegistryError(f"network request failed: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise OfficialRegistryError(
            f"curl failed (exit {proc.returncode}){': ' + detail if detail else ''}"
        )

    body = proc.stdout.decode("utf-8", errors="replace").strip()
    if not body:
        raise OfficialRegistryError("empty response from official registry")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OfficialRegistryError(f"invalid JSON from official registry: {exc}") from exc


def fetch_page(search: str = None, cursor: str = None, limit: int = 100,
               timeout: float = 15.0) -> dict:
    """Fetch one page of `/v0/servers`. Honors the test-fixture / fail env vars."""
    if os.environ.get("MCP_OFFICIAL_FAIL"):
        raise OfficialRegistryError("simulated network failure")

    fixture = os.environ.get("MCP_OFFICIAL_FIXTURE")
    if fixture:
        try:
            with open(fixture) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise OfficialRegistryError(f"could not read fixture: {exc}") from exc
        servers = data.get("servers", [])
        if search:
            q = search.lower()
            servers = [it for it in servers if q in searchable_text(it)]
        # Fixture is treated as a single page (no cursor → no pagination).
        return {"servers": servers, "metadata": {}}

    params = {"limit": limit}
    if search:
        params["search"] = search
    if cursor:
        params["cursor"] = cursor
    url = f"{OFFICIAL_BASE}/v0/servers?{urlencode(params)}"
    return _curl_json(url, timeout=timeout)


def iter_servers(search: str = None, max_pages: int = 5, limit: int = 100,
                 timeout: float = 15.0):
    """Yield raw server items across up to `max_pages` pages."""
    cursor = None
    for _ in range(max_pages):
        page = fetch_page(search=search, cursor=cursor, limit=limit, timeout=timeout)
        for item in page.get("servers", []):
            yield item
        cursor = (page.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break


# ── Pure helpers (importable, no network) ─────────────────────────────────────

def official_meta(item: dict) -> dict:
    return (item.get("_meta") or {}).get(_META_KEY, {}) or {}


def is_latest_active(item: dict) -> bool:
    meta = official_meta(item)
    return meta.get("isLatest") is True and meta.get("status") == "active"


def searchable_text(item: dict) -> str:
    srv = item.get("server", {}) or {}
    return " ".join([
        srv.get("name", ""),
        srv.get("title", ""),
        srv.get("description", ""),
    ]).lower()


def normalize(item: dict) -> dict:
    """Reduce a raw registry item to the fields the CLI displays."""
    srv = item.get("server", {}) or {}
    kind = "remote" if srv.get("remotes") else ("package" if srv.get("packages") else "unknown")
    return {
        "name": srv.get("name", ""),
        "title": srv.get("title", "") or srv.get("name", ""),
        "description": srv.get("description", ""),
        "version": srv.get("version", ""),
        "kind": kind,
        "source": "official",
    }


def search_servers(query: str, limit: int = 20, max_pages: int = 3,
                   timeout: float = 15.0):
    """Search the official registry. Returns (results, total_matched).

    Only latest+active versions are kept, de-duplicated by name, and filtered
    with a client-side substring guard (the API's search can be fuzzy).
    """
    q = query.lower()
    seen = set()
    matched = []
    for item in iter_servers(search=query, max_pages=max_pages, limit=limit * 3,
                             timeout=timeout):
        if not is_latest_active(item):
            continue
        name = item.get("server", {}).get("name", "")
        if name in seen:
            continue
        if q not in searchable_text(item):
            continue
        seen.add(name)
        matched.append(normalize(item))
    return matched[:limit], len(matched)


def get_server(name: str, max_pages: int = 5, timeout: float = 15.0):
    """Resolve a single server by exact name to its latest active version."""
    matches = []
    for item in iter_servers(search=name, max_pages=max_pages, timeout=timeout):
        if item.get("server", {}).get("name") == name:
            matches.append(item)
    if not matches:
        return None
    # Prefer the version flagged latest+active.
    for item in matches:
        if is_latest_active(item):
            return item
    # Fallback: highest version among active (or any) versions.
    active = [it for it in matches if official_meta(it).get("status") == "active"]
    pool = active or matches
    return max(pool, key=lambda it: it.get("server", {}).get("version", ""))


# ── Config mapping ────────────────────────────────────────────────────────────

def official_server_id(name: str) -> str:
    """Turn a namespaced registry name into a safe config key.

    e.g. 'com.notion/mcp' -> 'com_notion_mcp'
    """
    out = "".join(c if c.isalnum() else "_" for c in name)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_").lower()


def _env_required_from(entries) -> list:
    """Normalize an official environmentVariables/headers list to env_required."""
    out = []
    for e in entries or []:
        item = {"name": e.get("name", ""), "description": e.get("description", "")}
        out.append(item)
    return [e for e in out if e["name"]]


def to_gateway_entry(item: dict):
    """Map a registry item to a (gateway_entry, env_required) pair.

    remotes  -> {"type": "http", "url": ...}   (+ headers placeholders if required)
    packages -> {"type": "stdio", "command": ..., "args": [...]}  (+ env placeholders)

    Raises OfficialRegistryError if the server can't be mapped.
    """
    srv = item.get("server", {}) or {}

    remotes = srv.get("remotes") or []
    if remotes:
        remote = remotes[0]
        url = remote.get("url", "")
        if not url:
            raise OfficialRegistryError("remote has no url")
        entry = {"type": "http", "url": url}
        env_required = _env_required_from(remote.get("headers"))
        if env_required:
            entry["headers"] = {e["name"]: f"${{{e['name']}}}" for e in env_required}
        return entry, env_required

    packages = srv.get("packages") or []
    if packages:
        pkg = packages[0]
        rt = pkg.get("registryType")
        ident = pkg.get("identifier", "")
        version = pkg.get("version")
        if not ident:
            raise OfficialRegistryError("package has no identifier")

        env_required = _env_required_from(pkg.get("environmentVariables"))

        if rt == "npm":
            spec = f"{ident}@{version}" if version else ident
            entry = {"type": "stdio", "command": "npx", "args": ["-y", spec]}
        elif rt == "pypi":
            entry = {"type": "stdio", "command": "uvx", "args": [ident]}
        elif rt == "oci":
            args = ["run", "-i", "--rm"]
            for e in env_required:
                args += ["-e", e["name"]]
            args.append(ident)
            entry = {"type": "stdio", "command": "docker", "args": args}
        else:
            raise OfficialRegistryError(
                f"unsupported package type '{rt}' (only npm, pypi, oci are mapped)"
            )

        if env_required:
            entry["env"] = {e["name"]: f"${{{e['name']}}}" for e in env_required}
        return entry, env_required

    raise OfficialRegistryError("server exposes neither remotes nor packages")
