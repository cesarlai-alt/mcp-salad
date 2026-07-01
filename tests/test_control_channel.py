"""
Tests for the out-of-band control channel (the `mcp enable/disable` live-toggle).

We can't stand up a full Claude Code + gateway session in CI, so we test the
pieces that ARE testable in isolation:

1. CLI graceful failure when no control socket exists (friendly message, non-zero
   exit, no traceback).
2. Full CLI client-protocol round-trip against a minimal stub Unix-socket server
   that speaks the same newline protocol — proves the CLI's client is correct
   without needing the real gateway.
3. Pure unit tests for the gateway's `parse_control_line` parser and the
   `handle_control_command` router (config-independent commands only).

Async server pieces are driven with asyncio in a background thread; the CLI is
exercised as a subprocess (mirroring test_phase2.py's style).
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).parent.parent
CLI = REPO_ROOT / "cli" / "mcp.py"
GATEWAY_DIR = REPO_ROOT / "gateway"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@pytest.fixture
def short_sock():
    """A short Unix-socket path.

    macOS caps AF_UNIX paths at ~104 chars; pytest's tmp_path lives under a long
    /var/folders/... prefix that overflows it, so we bind under /tmp instead.
    """
    d = tempfile.mkdtemp(dir="/tmp")
    try:
        yield Path(d) / "g.sock"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_cli(*args, sock_path: Path = None):
    """Run the CLI as a subprocess with an optional injected control socket path."""
    env = os.environ.copy()
    if sock_path is not None:
        env["MCP_SALAD_CONTROL_SOCK"] = str(sock_path)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class StubControlServer:
    """A minimal asyncio Unix-socket server speaking the control protocol.

    Runs its own event loop in a background thread. `enable x` → 'ok: enabled x
    (3 tools)', `disable x` → 'ok: disabled x', `ping` → 'pong', else an error.
    """

    def __init__(self, sock_path: Path):
        self.sock_path = str(sock_path)
        self._thread = None
        self._ready = threading.Event()
        self._stop = threading.Event()

    async def _handle(self, reader, writer):
        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode().strip()
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else ""
            if cmd == "enable":
                reply = f"ok: enabled {arg} (3 tools)"
            elif cmd == "disable":
                reply = f"ok: disabled {arg}"
            elif cmd == "ping":
                reply = "pong"
            else:
                reply = f"error: unknown command '{cmd}'"
            writer.write((reply + "\n").encode())
            await writer.drain()
        writer.close()

    def _serve(self):
        async def main():
            try:
                os.unlink(self.sock_path)
            except FileNotFoundError:
                pass
            server = await asyncio.start_unix_server(self._handle, path=self.sock_path)
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.05)
            server.close()
            await server.wait_closed()

        asyncio.run(main())

    def __enter__(self):
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "stub control server did not start"
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------- #
# 1. CLI graceful failure — no socket present
# --------------------------------------------------------------------------- #

def test_enable_no_socket_is_graceful(tmp_path):
    missing = tmp_path / "nope.sock"
    result = run_cli("enable", "twstock", sock_path=missing)

    assert result.returncode != 0, "expected non-zero exit when gateway isn't running"
    combined = result.stdout + result.stderr
    assert "Gateway isn't running" in combined, combined
    assert str(missing) in combined
    assert "Traceback" not in combined, f"CLI leaked a traceback:\n{combined}"


def test_disable_no_socket_is_graceful(tmp_path):
    missing = tmp_path / "nope.sock"
    result = run_cli("disable", "twstock", sock_path=missing)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Gateway isn't running" in combined
    assert "Traceback" not in combined


# --------------------------------------------------------------------------- #
# 2. Round-trip against a stub socket server — proves the CLI client protocol
# --------------------------------------------------------------------------- #

def test_enable_round_trip_against_stub(short_sock):
    sock_path = short_sock
    with StubControlServer(sock_path):
        result = run_cli("enable", "x", sock_path=sock_path)

    assert result.returncode == 0, (
        f"expected exit 0 on 'ok' reply; got {result.returncode}\n"
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "ok: enabled x (3 tools)" in result.stdout, result.stdout


def test_disable_round_trip_against_stub(short_sock):
    sock_path = short_sock
    with StubControlServer(sock_path):
        result = run_cli("disable", "x", sock_path=sock_path)

    assert result.returncode == 0, result.stderr
    assert "ok: disabled x" in result.stdout, result.stdout


# --------------------------------------------------------------------------- #
# 3. Gateway-side pure unit tests: parse_control_line + handle_control_command
# --------------------------------------------------------------------------- #
#
# Imported lazily inside the tests so this module's import does NOT trigger the
# router's config load at collection time (test_router_integration.py must be the
# one that imports router first, with its own MCP_ROUTER_CONFIG override).

def _import_router():
    if str(GATEWAY_DIR) not in sys.path:
        sys.path.insert(0, str(GATEWAY_DIR))
    import router  # noqa: E402
    return router


def test_parse_control_line_routing():
    router = _import_router()

    assert router.parse_control_line("enable twstock") == ("enable", "twstock")
    assert router.parse_control_line("disable twstock") == ("disable", "twstock")
    assert router.parse_control_line("ping") == ("ping", None)
    # Extra whitespace is tolerated; command lower-cased.
    assert router.parse_control_line("  ENABLE   foo  ") == ("enable", "foo")
    # Garbage / empty.
    assert router.parse_control_line("") == ("", None)
    assert router.parse_control_line("   ") == ("", None)
    assert router.parse_control_line("bogus") == ("bogus", None)


def test_handle_control_command_config_independent():
    router = _import_router()

    async def scenario():
        assert await router.handle_control_command("ping") == "pong"

        # Unknown command → error, no crash.
        assert (await router.handle_control_command("frobnicate x")).startswith("error")

        # Empty line → error.
        assert (await router.handle_control_command("")).startswith("error")

        # enable without an arg → error.
        assert (await router.handle_control_command("enable")).startswith("error")

        # enable a server that no capability owns → error (works regardless of
        # which config happens to be loaded, since this id is nonsense).
        reply = await router.handle_control_command("enable __no_such_server__zzz")
        assert reply.startswith("error"), reply
        assert "no capability" in reply.lower()

    asyncio.run(scenario())


def test_handle_control_command_no_active_session(tmp_path, monkeypatch):
    """enable on a real capability with no MCP session → 'no active session' error.

    Uses a temp config so a capability actually exists to load, then verifies the
    control handler reports the missing session rather than crashing.
    """
    router = _import_router()

    # A capability exists to load, but there is no live MCP session, so the
    # handler must report that rather than crash. Monkeypatch the lookup +
    # loader so the test is hermetic (no child process spawned).
    monkeypatch.setattr(router, "_active_session", None, raising=False)
    monkeypatch.setattr(
        router, "find_capability_by_server", lambda sid: "some_cap" if sid == "srv1" else None
    )

    async def fake_load(cap_name):
        return [{"name": "srv1__a"}, {"name": "srv1__b"}]

    monkeypatch.setattr(router, "load_capability_tools", fake_load)

    async def scenario():
        reply = await router.handle_control_command("enable srv1")
        assert reply.startswith("error"), reply
        assert "no active session" in reply.lower(), reply

    asyncio.run(scenario())
