"""
Tests for the official MCP registry integration.

No network is used: the official client reads canned JSON when the env var
MCP_OFFICIAL_FIXTURE points at a fixture file, and raises a clean error when
MCP_OFFICIAL_FAIL is set. Parsing/normalization is tested by importing the
`official` module directly; end-to-end install/search is tested via the CLI
subprocess (matching the existing test style).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
CLI = REPO_ROOT / "cli" / "mcp.py"
FIXTURE = Path(__file__).parent / "fixtures" / "official_servers.json"

# Import the official client module directly for unit tests.
# Load by file path (NOT via sys.path) so we don't shadow the real `mcp`
# package with cli/mcp.py during collection of other test modules.
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("official", str(REPO_ROOT / "cli" / "official.py"))
official = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(official)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def run_cli(*args, config_path=None, env_extra=None):
    env = os.environ.copy()
    if config_path:
        env["MCP_GATEWAY_CONFIG"] = str(config_path)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )


def make_empty_config(tmp_path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump({"capabilities": {}, "servers": {}}))
    return cfg


def load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


@pytest.fixture(autouse=True)
def _clear_official_env(monkeypatch):
    """Ensure a clean slate for the process-local (import) tests."""
    monkeypatch.delenv("MCP_OFFICIAL_FAIL", raising=False)
    monkeypatch.setenv("MCP_OFFICIAL_FIXTURE", str(FIXTURE))


# --------------------------------------------------------------------------- #
# Parsing / normalization (in-process)
# --------------------------------------------------------------------------- #

class TestParsingAndFiltering:
    def test_search_keeps_only_latest_active(self):
        results, total = official.search_servers("widget", limit=20)
        names = [r["name"] for r in results]

        # The active, isLatest widget remote is kept.
        assert "com.example/widget" in names
        # The non-latest 0.9.0 version of the same server is de-duped/dropped.
        assert names.count("com.example/widget") == 1
        # The deprecated server (isLatest but status != active) is dropped.
        assert "io.deprecated/widget-gone" not in names

    def test_latest_active_version_selected(self):
        item = official.get_server("com.example/widget")
        assert item is not None
        assert item["server"]["version"] == "1.0.0"
        assert official.is_latest_active(item) is True

    def test_normalize_shapes(self):
        results, _ = official.search_servers("widget", limit=20)
        by_name = {r["name"]: r for r in results}
        assert by_name["com.example/widget"]["kind"] == "remote"
        assert by_name["io.acme/toolbox"]["kind"] == "package"
        assert by_name["io.acme/toolbox"]["source"] == "official"

    def test_is_latest_active_predicate(self):
        active_latest = {"_meta": {official._META_KEY: {"status": "active", "isLatest": True}}}
        not_latest = {"_meta": {official._META_KEY: {"status": "active", "isLatest": False}}}
        deprecated = {"_meta": {official._META_KEY: {"status": "deprecated", "isLatest": True}}}
        assert official.is_latest_active(active_latest) is True
        assert official.is_latest_active(not_latest) is False
        assert official.is_latest_active(deprecated) is False


# --------------------------------------------------------------------------- #
# to_gateway_entry mapping (in-process)
# --------------------------------------------------------------------------- #

class TestGatewayMapping:
    def test_remote_maps_to_http(self):
        item = official.get_server("com.example/widget")
        entry, env_required = official.to_gateway_entry(item)
        assert entry["type"] == "http"
        assert entry["url"] == "https://api.example.com/mcp"
        assert [e["name"] for e in env_required] == ["Authorization"]
        assert entry["headers"]["Authorization"] == "${Authorization}"

    def test_npm_package_maps_to_stdio_npx(self):
        item = official.get_server("io.acme/toolbox")
        entry, env_required = official.to_gateway_entry(item)
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert entry["args"][0] == "-y"
        assert "@acme/toolbox-mcp" in entry["args"][1]
        assert entry["env"]["ACME_API_KEY"] == "${ACME_API_KEY}"

    def test_pypi_package_maps_to_uvx(self):
        item = official.get_server("io.pytools/widget-cli")
        entry, _ = official.to_gateway_entry(item)
        assert entry["type"] == "stdio"
        assert entry["command"] == "uvx"
        assert entry["args"] == ["widget-cli-mcp"]

    def test_unmappable_package_raises(self):
        item = official.get_server("io.exotic/mystery")
        with pytest.raises(official.OfficialRegistryError):
            official.to_gateway_entry(item)


# --------------------------------------------------------------------------- #
# install (CLI subprocess, offline via fixture)
# --------------------------------------------------------------------------- #

class TestOfficialInstall:
    def test_install_remote_writes_http_entry(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("install", "com.example/widget", "-y",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FIXTURE": str(FIXTURE)})
        assert result.returncode == 0, result.stdout + result.stderr
        data = load_cfg(cfg)
        entry = data["servers"]["com_example_widget"]
        assert entry["type"] == "http"
        assert entry["url"] == "https://api.example.com/mcp"

    def test_install_npm_writes_stdio_npx_entry(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("install", "io.acme/toolbox", "-y",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FIXTURE": str(FIXTURE)})
        assert result.returncode == 0, result.stdout + result.stderr
        data = load_cfg(cfg)
        entry = data["servers"]["io_acme_toolbox"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "npx"
        assert entry["env"]["ACME_API_KEY"] == "${ACME_API_KEY}"

    def test_install_unmappable_fails_gracefully(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("install", "io.exotic/mystery", "-y",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FIXTURE": str(FIXTURE)})
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "unsupported package type" in (result.stdout + result.stderr).lower()

    def test_install_unknown_server_fails_gracefully(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("install", "does.not/exist", "-y",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FIXTURE": str(FIXTURE)})
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "not found" in (result.stdout + result.stderr).lower()


# --------------------------------------------------------------------------- #
# search behavior (CLI subprocess)
# --------------------------------------------------------------------------- #

class TestSearchSources:
    def test_search_local_offline_returns_curated(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        # Force any accidental network call to fail; --source local must not touch it.
        result = run_cli("search", "firecrawl", "--source", "local",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FAIL": "1"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "firecrawl" in result.stdout.lower()
        assert "curated" in result.stdout.lower()
        assert "official registry" not in result.stdout.lower()

    def test_search_official_finds_fixture_hit(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("search", "widget", "--source", "official",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FIXTURE": str(FIXTURE)})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "com.example/widget" in result.stdout
        assert "[official]" in result.stdout

    def test_search_official_failure_is_graceful(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("search", "widget", "--source", "official",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FAIL": "1"})
        # No traceback; a friendly message; command still exits cleanly.
        assert "Traceback" not in result.stderr
        assert "official registry" in result.stdout.lower()

    def test_search_all_failure_falls_back_to_local(self, tmp_path):
        cfg = make_empty_config(tmp_path)
        result = run_cli("search", "firecrawl", "--source", "all",
                         config_path=cfg,
                         env_extra={"MCP_OFFICIAL_FAIL": "1"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "firecrawl" in result.stdout.lower()
        assert "Traceback" not in result.stderr
