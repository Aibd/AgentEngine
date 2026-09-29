"""Tests for the MCP runtime bridge (adapters, connections, loader).

All transports and sessions are stubbed — no real MCP server process is
spawned except in the explicitly gated integration test at the bottom.
"""

from __future__ import annotations

import contextlib
import os
import sys
from types import SimpleNamespace
from typing import Any

import mcp.types as mcp_types
import pytest

from app.backend.services import mcp_runtime
from app.backend.services.mcp_connectors import McpConnector


def _mcp_tool(name: str, description: str = "", schema: dict | None = None) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
    )


class _FakeSession:
    """Stand-in for ``mcp.ClientSession`` returning canned tool results."""

    def __init__(
        self,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        return self._result


# -- McpToolAdapter ----------------------------------------------------------


async def test_adapter_run_joins_text_content() -> None:
    result = mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(type="text", text="hello"),
            mcp_types.TextContent(type="text", text="world"),
        ]
    )
    session = _FakeSession(result=result)
    adapter = mcp_runtime.McpToolAdapter(session, "fs", _mcp_tool("read"))

    output = await adapter.run(path="/tmp/a")

    assert output == "hello\nworld"
    assert session.calls == [("read", {"path": "/tmp/a"})]


async def test_adapter_run_converts_call_errors_to_string() -> None:
    session = _FakeSession(error=RuntimeError("connection reset"))
    adapter = mcp_runtime.McpToolAdapter(session, "fs", _mcp_tool("read"))

    output = await adapter.run()

    assert output.startswith("Error calling MCP tool read:")
    assert "connection reset" in output


async def test_adapter_name_slug_and_schema_passthrough() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    tool = _mcp_tool("search", "Search things", schema)
    adapter = mcp_runtime.McpToolAdapter(_FakeSession(), "My Files (local)!", tool)

    assert adapter.name == "mcp__My_Files__local____search"
    assert adapter.description == "Search things"
    assert adapter.schema == schema
    assert adapter.timeout_seconds == 60.0


# -- stdio parameter handling -------------------------------------------------


def test_stdio_params_merges_os_environ_and_appends_cmd_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("PATH", "C:\\fake-bin")
    connector = McpConnector(
        id="c1",
        name="fs",
        transport="stdio",
        command="npx",
        args=["-y", "server"],
        env={"API_KEY": "secret"},
    )

    params = mcp_runtime.McpRunConnection._stdio_params(connector)

    assert params.command == "npx.cmd"
    assert params.args == ["-y", "server"]
    assert params.env is not None
    assert params.env["API_KEY"] == "secret"
    assert params.env["PATH"] == "C:\\fake-bin"


def test_stdio_params_connector_env_wins_over_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("MCP_TEST_KEY", "from-os")
    connector = McpConnector(
        id="c1",
        name="fs",
        transport="stdio",
        command="uvx",
        env={"MCP_TEST_KEY": "from-connector"},
    )

    params = mcp_runtime.McpRunConnection._stdio_params(connector)

    assert params.command == "uvx"  # no .cmd shim off Windows
    assert params.env is not None
    assert params.env["MCP_TEST_KEY"] == "from-connector"


# -- load_mcp_tools -----------------------------------------------------------


class _FakeClientSession:
    """Async-context-manager session stub used by the transport fakes."""

    tools: list[mcp_types.Tool] = []

    def __init__(self, read: Any, write: Any) -> None:
        self.initialized = False

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=list(type(self).tools))


@contextlib.asynccontextmanager
async def _fake_stdio_client(params: Any) -> Any:
    if "boom" in (params.args or []):
        raise RuntimeError("spawn failed")
    yield ("read", "write")


async def test_load_mcp_tools_collects_tools_and_tolerates_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_runtime, "stdio_client", _fake_stdio_client)
    monkeypatch.setattr(mcp_runtime, "ClientSession", _FakeClientSession)
    _FakeClientSession.tools = [_mcp_tool("read", "Read a file")]

    good = McpConnector(id="1", name="fs", transport="stdio", command="server")
    bad = McpConnector(id="2", name="broken", transport="stdio", command="server", args=["boom"])
    disabled = McpConnector(
        id="3", name="off", transport="stdio", command="server", enabled=False
    )

    tools, connections = await mcp_runtime.load_mcp_tools([good, bad, disabled])

    # Only enabled connectors produce connections; the broken one yields no tools.
    assert len(connections) == 2
    assert [tool.name for tool in tools] == ["mcp__fs__read"]

    for connection in connections:
        await connection.close()


async def test_load_mcp_tools_skips_unknown_transport() -> None:
    weird = McpConnector(id="1", name="weird", transport="websocket")

    tools, connections = await mcp_runtime.load_mcp_tools([weird])

    assert tools == []
    assert len(connections) == 1
    await connections[0].close()


async def test_close_tolerates_underlying_errors() -> None:
    connection = mcp_runtime.McpRunConnection()

    class _Boom:
        async def __aenter__(self) -> "_Boom":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            raise RuntimeError("teardown exploded")

    await connection._exit_stack.enter_async_context(_Boom())
    # Must not raise.
    await connection.close()


# -- Real stdio round-trip (opt-in) -------------------------------------------

_INTEGRATION_SERVER = '''
from mcp.server import MCPServer

server = MCPServer("integration-test")

@server.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return f"echo:{text}"

server.run()
'''


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_MCP_INTEGRATION") != "1",
    reason="real MCP stdio subprocess; set RUN_MCP_INTEGRATION=1 to enable",
)
async def test_real_stdio_connector_round_trip(tmp_path: Any) -> None:
    server_script = tmp_path / "mcp_echo_server.py"
    server_script.write_text(_INTEGRATION_SERVER, encoding="utf-8")
    connector = McpConnector(
        id="itest",
        name="itest",
        transport="stdio",
        command=sys.executable,
        args=[str(server_script)],
    )

    tools, connections = await mcp_runtime.load_mcp_tools([connector])
    try:
        assert [tool.name for tool in tools] == ["mcp__itest__echo"]
        output = await tools[0].run(text="hi")
        assert output == "echo:hi"
    finally:
        for connection in connections:
            await connection.close()
