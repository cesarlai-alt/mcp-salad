"""
GENUINE end-to-end integration tests for the MCP Gateway router
(gateway/router.py).

Unlike test_phase2.py (which only exercises the CLI's file operations), these
tests spin up a REAL child MCP server (tests/fixtures/fake_mcp_server.py) over
stdio and drive the router's actual runtime: spawning the child, the MCP
handshake, tools/list, tools/call, capability loading/unloading, name
namespacing, the auto_use_capability single-step proxy path, and graceful
failure handling.

Async is driven with asyncio.run(...) inside sync test functions because
pytest-asyncio is not installed in this environment.

Config isolation: router.py reads its config at import time from the path in
the MCP_ROUTER_CONFIG env var (falling back to gateway/config.yaml). We set that
env var to a temp config that points a capability at the fake server BEFORE
importing the router module.
"""

import asyncio
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

# --------------------------------------------------------------------------- #
# Paths (mirror how test_phase2.py locates files)
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).parent.parent
GATEWAY_DIR = REPO_ROOT / "gateway"
FAKE_SERVER = REPO_ROOT / "tests" / "fixtures" / "fake_mcp_server.py"

# --------------------------------------------------------------------------- #
# Build an isolated temp config pointing at the fake MCP server, then import the
# router with MCP_ROUTER_CONFIG pointed at it. This must happen BEFORE `import
# router`, because router.py loads config at module import time.
# --------------------------------------------------------------------------- #

_TMP_DIR = tempfile.mkdtemp(prefix="router_it_")
_CONFIG_PATH = Path(_TMP_DIR) / "config.yaml"

_CONFIG_DATA = {
    "capabilities": {
        "fake_cap": {
            "description": "Fake echo/add capability for integration tests",
            "keywords": ["echo", "fake", "add numbers", "roundtrip"],
            "servers": ["fakeserver"],
        },
        "broken_cap": {
            "description": "Capability whose server binary does not exist",
            "keywords": ["broken", "missing binary"],
            "servers": ["missingserver"],
        },
    },
    "servers": {
        "fakeserver": {
            "type": "stdio",
            "command": "python3",
            "args": [str(FAKE_SERVER)],
        },
        "missingserver": {
            "type": "stdio",
            "command": "xyz-missing-binary-99999",
            "args": [],
        },
    },
}
_CONFIG_PATH.write_text(yaml.dump(_CONFIG_DATA))

os.environ["MCP_ROUTER_CONFIG"] = str(_CONFIG_PATH)

# Make the gateway dir importable (mirror test_phase2's path handling style).
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

# Import the real runtime under test.
import router  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _clean_router_state():
    """
    Reset the router's module-level runtime state before each test and tear down
    any spawned child MCP processes afterward so tests don't leak processes or
    bleed state into each other.
    """
    router.active_capabilities.clear()
    router.server_tool_cache.clear()
    router._clients.clear()
    yield
    # Teardown: terminate every spawned child process. We signal by PID
    # directly (rather than asyncio.run(router.shutdown())) because each test
    # ran in its own event loop that is now closed; touching the asyncio
    # Process transport on a closed loop would emit noisy warnings.
    import signal
    for proc in list(router.child_processes.values()):
        pid = getattr(proc, "pid", None)
        if pid is None:
            continue
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                break
    router.child_processes.clear()
    router._clients.clear()
    router.active_capabilities.clear()
    router.server_tool_cache.clear()


def _text_of(result) -> str:
    """Join the text of a list[types.TextContent] returned by the call_tool handler."""
    return "\n".join(getattr(block, "text", "") for block in result)


# --------------------------------------------------------------------------- #
# 0. Sanity — config override actually took effect
# --------------------------------------------------------------------------- #

def test_config_override_loaded_fake_capability():
    assert "fake_cap" in router.CAPABILITIES, (
        "MCP_ROUTER_CONFIG override did not take effect — router loaded the "
        "wrong config."
    )
    assert "fakeserver" in router.SERVER_DEFS
    assert str(router.CONFIG_PATH) == str(_CONFIG_PATH)


# --------------------------------------------------------------------------- #
# 1. StdioMCPClient end-to-end: real child spawn + MCP protocol
# --------------------------------------------------------------------------- #

def test_stdio_client_list_and_call_tools_end_to_end():
    async def scenario():
        client = router.get_client("fakeserver")
        assert isinstance(client, router.StdioMCPClient)

        tools = await client.list_tools()
        names = {t["name"] for t in tools}
        assert {"echo", "add"} <= names, f"expected echo/add, got {names}"

        echo_res = await client.call_tool("echo", {"text": "hi"})
        assert echo_res["content"][0]["text"] == "hi"

        add_res = await client.call_tool("add", {"a": 2, "b": 3})
        assert add_res["content"][0]["text"] == "5"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 2. load_capability_tools returns correctly PREFIXED tool names
# --------------------------------------------------------------------------- #

def test_load_capability_tools_prefixes_names():
    async def scenario():
        tools = await router.load_capability_tools("fake_cap")
        names = {t["name"] for t in tools}
        assert "fakeserver__echo" in names, names
        assert "fakeserver__add" in names, names
        # Capability is now marked active.
        assert "fake_cap" in router.active_capabilities

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3. proxy_tool_name / parse_proxy_tool_name round-trip
# --------------------------------------------------------------------------- #

def test_proxy_tool_name_round_trip():
    proxied = router.proxy_tool_name("fakeserver", "echo")
    assert proxied == "fakeserver__echo"
    parsed = router.parse_proxy_tool_name(proxied)
    assert parsed == ("fakeserver", "echo")

    # A tool name that itself contains the separator still splits on the first.
    proxied2 = router.proxy_tool_name("srv", "some__tool")
    assert router.parse_proxy_tool_name(proxied2) == ("srv", "some__tool")

    # No separator → None.
    assert router.parse_proxy_tool_name("noseparator") is None


# --------------------------------------------------------------------------- #
# 4. auto_use_capability: load capability AND proxy the real call in one step
# --------------------------------------------------------------------------- #

def test_auto_use_capability_full_proxy_path():
    async def scenario():
        # No live MCP session exists in tests; the handler's
        # send_tool_list_changed() call is wrapped in try/except inside router,
        # so this runs without a real client session while keeping the actual
        # child-server call REAL.
        result = await router.call_tool(
            "auto_use_capability",
            {"tool_name": "fakeserver__echo", "arguments": {"text": "pong"}},
        )
        text = _text_of(result)
        assert "pong" in text, f"expected real echoed 'pong', got: {text!r}"
        # The capability was loaded as part of the single step.
        assert "fake_cap" in router.active_capabilities

    asyncio.run(scenario())


def test_auto_use_capability_bare_prefix_only_loads():
    async def scenario():
        result = await router.call_tool(
            "auto_use_capability", {"tool_name": "fakeserver"}
        )
        text = _text_of(result)
        assert "fake_cap" in router.active_capabilities
        assert "Loaded" in text or "loaded" in text

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 5. match_capability / find_capability_by_server
# --------------------------------------------------------------------------- #

def test_match_capability_keyword_lookup():
    assert router.match_capability("please echo this back") == "fake_cap"
    assert router.match_capability("I need a roundtrip test") == "fake_cap"
    assert router.match_capability("completely unrelated request xyzzy") is None


def test_find_capability_by_server():
    assert router.find_capability_by_server("fakeserver") == "fake_cap"
    assert router.find_capability_by_server("missingserver") == "broken_cap"
    assert router.find_capability_by_server("does_not_exist") is None


# --------------------------------------------------------------------------- #
# 6. Graceful failure: missing binary / bad tool name — no unhandled traceback
# --------------------------------------------------------------------------- #

def test_missing_binary_fails_gracefully_via_handler():
    async def scenario():
        # Route a proxy call to a server whose command binary doesn't exist.
        result = await router.call_tool("missingserver__echo", {"text": "x"})
        text = _text_of(result).lower()
        assert "error" in text, f"expected an error message, got: {text!r}"

    # Must NOT raise out of the handler.
    asyncio.run(scenario())


def test_bad_tool_name_on_fake_server_fails_gracefully():
    async def scenario():
        # Load the capability first, then call a tool that doesn't exist on the
        # fake server. The fake server raises → low-level MCP returns an isError
        # result; the router surfaces it without crashing.
        await router.load_capability_tools("fake_cap")
        result = await router.call_tool("fakeserver__nonexistent_tool", {})
        text = _text_of(result)
        # Either an "Error calling ..." wrapper or an isError content payload —
        # the key requirement is that a result came back and nothing was raised.
        assert isinstance(result, list) and len(result) >= 1
        assert text, "expected some error text, got empty result"

    asyncio.run(scenario())


def test_auto_use_capability_unknown_server_no_crash():
    async def scenario():
        result = await router.call_tool(
            "auto_use_capability", {"tool_name": "totallyunknown__foo", "arguments": {}}
        )
        text = _text_of(result)
        assert "No capability found" in text or "capabilities" in text.lower()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 7. unload_capability — tools no longer active afterward
# --------------------------------------------------------------------------- #

def test_unload_capability_deactivates_tools():
    async def scenario():
        await router.load_capability_tools("fake_cap")
        assert "fake_cap" in router.active_capabilities

        # Active tool list (via the MCP list_tools handler) includes the proxied
        # tools while loaded.
        active_names = {t.name for t in await router.list_tools()}
        assert "fakeserver__echo" in active_names

        assert router.unload_capability("fake_cap") is True
        assert "fake_cap" not in router.active_capabilities

        # After unload, the proxied tools are gone from the active tool list.
        active_names_after = {t.name for t in await router.list_tools()}
        assert "fakeserver__echo" not in active_names_after

        # Unloading again returns False (nothing to unload) without raising.
        assert router.unload_capability("fake_cap") is False

    asyncio.run(scenario())


def test_drop_capability_handler_end_to_end():
    async def scenario():
        await router.load_capability_tools("fake_cap")
        result = await router.call_tool("drop_capability", {"name": "fake_cap"})
        text = _text_of(result)
        assert "unloaded" in text.lower()
        assert "fake_cap" not in router.active_capabilities

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 8. restart_server — kill-and-relaunch a stdio server in place
#
# Regression coverage for the gap raised on GitHub issue #24057: `/mcp
# reconnect` only re-attaches to an already-running stdio subprocess, so
# redeployed-in-place code (same command/args, new script) is never picked up
# without a full session restart. restart_server() must actually terminate the
# old subprocess (not just clear config) and, if the capability was active,
# relaunch + refresh the tool schema so the client sees fresh state.
# --------------------------------------------------------------------------- #

class _StubSession:
    """Stand-in for the captured MCP ServerSession (no real client is attached
    in this test), just enough to satisfy _send_list_changed()'s call site."""
    async def send_tool_list_changed(self):
        return None


def test_restart_kills_and_relaunches_the_real_subprocess(monkeypatch):
    async def scenario():
        # Get the capability running first so there's a live child PID to kill.
        await router.load_capability_tools("fake_cap")
        old_client = router._clients["fakeserver"]
        old_proc = old_client.proc
        assert old_proc is not None and old_proc.returncode is None
        old_pid = old_proc.pid

        # restart_server() eagerly reloads and notifies via
        # _send_list_changed(), which needs a captured session — normally set
        # by _capture_active_session() on the first real client request. No
        # real MCP client is attached in this test, so stub it directly
        # (mirrors how test_control_channel.py handles the same dependency).
        monkeypatch.setattr(router, "_active_session", _StubSession())

        reply = await router.restart_server("fakeserver")
        assert reply.startswith("ok:"), reply

        # The old subprocess must actually be gone (terminated), not merely
        # forgotten — this is the whole point vs. a config-only no-op.
        assert old_proc.returncode is not None, "old subprocess was never terminated"

        # The capability was active, so restart_server should have eagerly
        # relaunched it: a fresh client/process should already exist, tools
        # still work, and the tool list is intact.
        assert "fakeserver" in router._clients
        new_client = router._clients["fakeserver"]
        assert new_client.proc is not None and new_client.proc.returncode is None
        assert new_client.proc.pid != old_pid, "expected a genuinely new subprocess"

        echo_res = await new_client.call_tool("echo", {"text": "after-restart"})
        assert echo_res["content"][0]["text"] == "after-restart"
        assert "fake_cap" in router.active_capabilities

    asyncio.run(scenario())


def test_restart_when_capability_not_active_just_clears_cache():
    async def scenario():
        # Never loaded in this scenario — nothing active, nothing running yet.
        assert "fake_cap" not in router.active_capabilities

        reply = await router.restart_server("fakeserver")
        assert reply.startswith("ok:"), reply
        assert "will start fresh" in reply

        # No eager relaunch when the capability wasn't active — lazy-start on
        # next real use is fine and cheaper.
        assert "fakeserver" not in router._clients
        assert "fake_cap" not in router.active_capabilities

    asyncio.run(scenario())


def test_restart_unknown_server_errors_gracefully():
    async def scenario():
        reply = await router.restart_server("no_such_server_xyz")
        assert reply.startswith("error:"), reply
        assert "unknown server id" in reply.lower()

    asyncio.run(scenario())


def test_restart_http_server_rejected():
    async def scenario():
        # Register an ad-hoc HTTP server def for this one test without
        # mutating the shared module-level SERVER_DEFS permanently.
        router.SERVER_DEFS["http_srv"] = {"type": "http", "url": "http://localhost:1"}
        try:
            reply = await router.restart_server("http_srv")
            assert reply.startswith("error:"), reply
            assert "http" in reply.lower()
        finally:
            del router.SERVER_DEFS["http_srv"]

    asyncio.run(scenario())


def test_control_command_restart_routes_correctly():
    async def scenario():
        reply = await router.handle_control_command("restart fakeserver")
        assert reply.startswith("ok:"), reply

        # Missing arg → error, no crash.
        reply2 = await router.handle_control_command("restart")
        assert reply2.startswith("error"), reply2

    asyncio.run(scenario())


def test_restart_failure_path_still_notifies_client(monkeypatch):
    """If reload fails after unload, the client must still be told the tool
    list changed — otherwise it keeps calling now-dead tool names into a
    black hole, believing the capability is still loaded."""
    async def scenario():
        await router.load_capability_tools("fake_cap")
        monkeypatch.setattr(router, "_active_session", _StubSession())

        notified = {"count": 0}
        real_send = router._send_list_changed

        async def counting_send():
            notified["count"] += 1
            return await real_send()

        monkeypatch.setattr(router, "_send_list_changed", counting_send)

        async def failing_load(cap_name):
            raise RuntimeError("simulated reload failure")

        monkeypatch.setattr(router, "load_capability_tools", failing_load)

        reply = await router.restart_server("fakeserver")
        assert reply.startswith("error:"), reply
        assert "failed to reload" in reply

        assert notified["count"] == 1, (
            "expected _send_list_changed to be called on the failure path "
            "so the client re-fetches tools/list instead of calling into a "
            "capability that was already unloaded"
        )

    asyncio.run(scenario())


def test_concurrent_restarts_do_not_orphan_a_subprocess(monkeypatch):
    """Two overlapping restart_server() calls for the same server_id must not
    each spawn their own replacement — the per-server lock should serialize
    them so only one relaunch actually happens at a time."""
    async def scenario():
        await router.load_capability_tools("fake_cap")
        monkeypatch.setattr(router, "_active_session", _StubSession())

        results = await asyncio.gather(
            router.restart_server("fakeserver"),
            router.restart_server("fakeserver"),
        )
        assert all(r.startswith("ok:") for r in results), results

        # Exactly one live client/process should exist afterward — not a
        # leaked orphan from the two calls racing each other.
        assert "fakeserver" in router._clients
        final_client = router._clients["fakeserver"]
        assert final_client.proc is not None and final_client.proc.returncode is None

        # And it still actually works.
        res = await final_client.call_tool("echo", {"text": "post-race"})
        assert res["content"][0]["text"] == "post-race"

    asyncio.run(scenario())
