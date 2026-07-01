"""
Phase 2 tests for mcp install / uninstall / list (gateway config management).

Expected behavior:
  - `mcp install <name>`   → adds server to gateway config.yaml (servers section)
  - `mcp uninstall <name>` → removes server from gateway config.yaml
  - `mcp list`             → lists servers currently installed in gateway config
  - `mcp install <bad>`    → exits non-zero with an error message

Config path is injected via env var MCP_GATEWAY_CONFIG so tests never touch
the real config and run fully isolated.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).parent.parent
CLI = REPO_ROOT / "cli" / "mcp.py"
EXAMPLE_CONFIG = REPO_ROOT / "gateway" / "config.example.yaml"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def run_cli(*args, config_path: Path = None, env_extra: dict = None):
    """Run the CLI as a subprocess with an optional injected gateway config."""
    env = os.environ.copy()
    if config_path:
        env["MCP_GATEWAY_CONFIG"] = str(config_path)
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    return result


def make_temp_config(base: Path = None) -> Path:
    """
    Copy config.example.yaml (or create a minimal one) into a temp directory.
    Returns the path to the copied config.yaml.
    """
    tmp_dir = tempfile.mkdtemp(prefix="mcp_test_")
    dest = Path(tmp_dir) / "config.yaml"

    if base and base.exists():
        shutil.copy(base, dest)
    else:
        # Minimal valid gateway config with no servers installed
        minimal = {"capabilities": {}, "servers": {}}
        dest.write_text(yaml.dump(minimal))

    return dest


def load_config(config_path: Path) -> dict:
    """Load YAML config and return as dict."""
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def empty_config(tmp_path):
    """An empty gateway config with no installed servers."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump({"capabilities": {}, "servers": {}}))
    return cfg


@pytest.fixture
def config_with_firecrawl(tmp_path):
    """A gateway config that already has firecrawl installed."""
    data = {
        "capabilities": {},
        "servers": {
            "firecrawl": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "firecrawl-mcp"],
            }
        },
    }
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.dump(data))
    return cfg


# --------------------------------------------------------------------------- #
# Tests: mcp list
# --------------------------------------------------------------------------- #

class TestMcpList:
    def test_list_empty_config_exits_ok(self, empty_config):
        result = run_cli("list", config_path=empty_config)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"

    def test_list_shows_installed_server(self, config_with_firecrawl):
        result = run_cli("list", config_path=config_with_firecrawl)
        assert result.returncode == 0
        assert "firecrawl" in result.stdout.lower(), (
            f"Expected 'firecrawl' in stdout but got:\n{result.stdout}"
        )

    def test_list_empty_config_shows_none_or_empty(self, empty_config):
        result = run_cli("list", config_path=empty_config)
        assert result.returncode == 0
        # Either "no servers" message or an empty table — either is acceptable
        output = result.stdout.lower()
        assert "firecrawl" not in output, "Empty config should not list firecrawl"


# --------------------------------------------------------------------------- #
# Tests: mcp install
# --------------------------------------------------------------------------- #

class TestMcpInstall:
    def test_install_firecrawl_exits_ok(self, empty_config):
        result = run_cli("install", "firecrawl", config_path=empty_config)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_install_firecrawl_writes_to_config(self, empty_config):
        run_cli("install", "firecrawl", config_path=empty_config)
        data = load_config(empty_config)
        servers = data.get("servers", {})
        assert "firecrawl" in servers, (
            f"Expected 'firecrawl' key in servers section after install, got: {list(servers.keys())}"
        )

    def test_install_firecrawl_config_has_command(self, empty_config):
        run_cli("install", "firecrawl", config_path=empty_config)
        data = load_config(empty_config)
        entry = data.get("servers", {}).get("firecrawl", {})
        assert "command" in entry or "url" in entry, (
            f"Installed server entry should have 'command' or 'url', got: {entry}"
        )

    def test_install_appears_in_list_afterward(self, empty_config):
        run_cli("install", "firecrawl", config_path=empty_config)
        result = run_cli("list", config_path=empty_config)
        assert "firecrawl" in result.stdout.lower(), (
            f"After install, 'mcp list' should show firecrawl.\nstdout: {result.stdout}"
        )

    def test_install_idempotent_no_crash(self, config_with_firecrawl):
        """Installing an already-installed server should not crash."""
        result = run_cli("install", "firecrawl", config_path=config_with_firecrawl)
        assert result.returncode == 0, (
            f"Re-installing an existing server crashed (exit {result.returncode})\nstderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- #
# Tests: mcp uninstall
# --------------------------------------------------------------------------- #

class TestMcpUninstall:
    def test_uninstall_firecrawl_exits_ok(self, config_with_firecrawl):
        result = run_cli("uninstall", "firecrawl", config_path=config_with_firecrawl)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_uninstall_removes_from_config(self, config_with_firecrawl):
        run_cli("uninstall", "firecrawl", config_path=config_with_firecrawl)
        data = load_config(config_with_firecrawl)
        servers = data.get("servers", {})
        assert "firecrawl" not in servers, (
            f"Expected firecrawl removed from servers, still present: {list(servers.keys())}"
        )

    def test_uninstall_gone_from_list(self, config_with_firecrawl):
        run_cli("uninstall", "firecrawl", config_path=config_with_firecrawl)
        result = run_cli("list", config_path=config_with_firecrawl)
        assert "firecrawl" not in result.stdout.lower(), (
            f"After uninstall, 'mcp list' should not show firecrawl.\nstdout: {result.stdout}"
        )

    def test_uninstall_nonexistent_fails_gracefully(self, empty_config):
        """Uninstalling something that isn't installed should not raise an exception."""
        result = run_cli("uninstall", "firecrawl", config_path=empty_config)
        # We accept either non-zero exit with an error message, or exit 0 with a
        # "not installed" notice — what's NOT acceptable is an unhandled traceback.
        assert "traceback" not in result.stderr.lower(), (
            f"Uninstalling a non-existent server raised an unhandled exception:\n{result.stderr}"
        )
        assert (
            "error" in result.stdout.lower()
            or "not found" in result.stdout.lower()
            or "not installed" in result.stdout.lower()
            or result.returncode != 0
        ), "Expected either a non-zero exit or an error/not-found/not-installed message in stdout"


# --------------------------------------------------------------------------- #
# Tests: error handling
# --------------------------------------------------------------------------- #

class TestErrorHandling:
    def test_install_nonexistent_server_fails(self, empty_config):
        result = run_cli("install", "nonexistent-server-xyz", config_path=empty_config)
        assert result.returncode != 0, (
            f"Expected non-zero exit for unknown server, got 0\nstdout: {result.stdout}"
        )

    def test_install_nonexistent_server_error_message(self, empty_config):
        result = run_cli("install", "nonexistent-server-xyz", config_path=empty_config)
        combined = (result.stdout + result.stderr).lower()
        assert "not found" in combined or "error" in combined or "unknown" in combined, (
            f"Expected an error/not-found message for unknown server.\ncombined output:\n{combined}"
        )

    def test_install_nonexistent_no_traceback(self, empty_config):
        result = run_cli("install", "nonexistent-server-xyz", config_path=empty_config)
        assert "traceback" not in result.stderr.lower(), (
            f"Unknown server install raised an unhandled exception:\n{result.stderr}"
        )


# --------------------------------------------------------------------------- #
# Smoke: existing Phase 1 commands still work
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Tests: mcp doctor
# --------------------------------------------------------------------------- #

class TestMcpDoctor:
    def test_doctor_empty_config_no_servers(self, empty_config):
        """Doctor with empty config prints 'No servers installed' and exits 0."""
        result = run_cli("doctor", config_path=empty_config)
        assert result.returncode == 0, (
            f"Expected exit 0 for empty config, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = (result.stdout + result.stderr).lower()
        assert "no servers" in combined or "not installed" in combined, (
            f"Expected 'no servers' or 'not installed' message.\nOutput:\n{result.stdout}"
        )

    def test_doctor_python3_server_shows_ok(self, tmp_path):
        """Doctor with python3 as command shows checkmark (python3 is always available)."""
        data = {
            "capabilities": {
                "test_cap": {
                    "description": "test capability",
                    "keywords": ["test"],
                    "servers": ["test_server"],
                }
            },
            "servers": {
                "test_server": {
                    "type": "stdio",
                    "command": "python3",
                    "args": ["some_script.py"],
                }
            },
        }
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(data))

        result = run_cli("doctor", config_path=cfg)
        # python3 is definitely on PATH since we're running the CLI with it
        assert result.returncode == 0, (
            f"Expected exit 0 when python3 is found, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "✅" in combined or "command found" in combined.lower(), (
            f"Expected ✅ or 'command found' in output.\nOutput:\n{combined}"
        )

    def test_doctor_missing_command_shows_error(self, tmp_path):
        """Doctor with a non-existent command shows ❌ and exits 1."""
        data = {
            "capabilities": {},
            "servers": {
                "broken_server": {
                    "type": "stdio",
                    "command": "xyz-missing-binary-99999",
                    "args": [],
                }
            },
        }
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump(data))

        result = run_cli("doctor", config_path=cfg)
        assert result.returncode == 1, (
            f"Expected exit 1 when command not found, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "❌" in combined or "not found" in combined.lower(), (
            f"Expected ❌ or 'not found' in output.\nOutput:\n{combined}"
        )

    def test_doctor_shows_server_count(self, config_with_firecrawl):
        """Doctor header shows the number of installed servers."""
        result = run_cli("doctor", config_path=config_with_firecrawl)
        # Should show count — either "1" in "Installed servers: 1" line
        assert "1" in result.stdout, (
            f"Expected server count '1' in output.\nOutput:\n{result.stdout}"
        )


# --------------------------------------------------------------------------- #
# Smoke: existing Phase 1 commands still work
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Tests: mcp upgrade
# --------------------------------------------------------------------------- #

class TestMcpUpgrade:
    def test_upgrade_installed_server_exits_ok(self, config_with_firecrawl):
        """Upgrading an installed server exits 0."""
        result = run_cli("upgrade", "firecrawl", config_path=config_with_firecrawl)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_upgrade_noninstalled_server_no_crash(self, empty_config):
        """Upgrading a server that isn't installed exits 0 with a warning, no traceback."""
        result = run_cli("upgrade", "firecrawl", config_path=empty_config)
        assert "traceback" not in result.stderr.lower(), (
            f"Upgrading a non-installed server raised an unhandled exception:\n{result.stderr}"
        )
        assert result.returncode == 0, (
            f"Expected exit 0 (graceful warning) for non-installed server, "
            f"got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- #
# Smoke: existing Phase 1 commands still work
# --------------------------------------------------------------------------- #

class TestPhase1Regression:
    """Quick sanity checks that Phase 1 commands weren't broken by Phase 2."""

    def test_search_still_works(self):
        result = run_cli("search", "firecrawl")
        assert result.returncode == 0
        assert "firecrawl" in result.stdout.lower()

    def test_info_still_works(self):
        result = run_cli("info", "firecrawl")
        assert result.returncode == 0
        assert "firecrawl" in result.stdout.lower()
