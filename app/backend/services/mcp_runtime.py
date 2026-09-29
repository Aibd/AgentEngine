"""Runtime bridge between configured MCP connectors and agent runs.

``mcp_connectors.py`` only stores connector *configuration*. This module
turns an enabled :class:`McpConnector` into live ``Tool`` instances that the
agent can call during a single run, and owns the lifecycle of the underlying
transports (stdio subprocesses / SSE streams) so they are closed when the run
ends.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any

import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

from agentengine.tools.base import Tool
from app.backend.services.mcp_connectors import McpConnector

logger = logging.getLogger(__name__)

# Windows resolves these launchers only via their ``.cmd`` shim.
_WINDOWS_CMD_SHIMS = {"npx", "npm", "uvx"}


def _slugify(name: str) -> str:
    """Make a connector name safe to embed in a tool name."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


class McpToolAdapter(Tool):
    """Adapt a single MCP tool to the agentengine ``Tool`` interface."""

    timeout_seconds = 60.0

    def __init__(
        self,
        session: ClientSession,
        connector_name: str,
        tool: mcp_types.Tool,
    ) -> None:
        self._session = session
        self._tool_name = tool.name
        self.name = f"mcp__{_slugify(connector_name)}__{tool.name}"
        self.description = tool.description or ""
        schema: dict[str, Any] = dict(tool.input_schema) if tool.input_schema else {}
        self.schema = schema or {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> str:
        try:
            result = await self._session.call_tool(self._tool_name, kwargs)
        except Exception as exc:
            return f"Error calling MCP tool {self._tool_name}: {exc}"
        if isinstance(result, mcp_types.CallToolResult):
            parts: list[str] = []
            for block in result.content:
                if isinstance(block, mcp_types.TextContent):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return str(result)


class McpRunConnection:
    """Owns the MCP transports opened for one run so they can be closed.

    Each connection wraps an ``AsyncExitStack``: the transport context and the
    ``ClientSession`` are entered on connect and unwound together on close.
    """

    def __init__(self) -> None:
        self._exit_stack = contextlib.AsyncExitStack()
        self.last_error: str | None = None

    async def connect(self, connector: McpConnector) -> list[Tool]:
        """Open the transport, initialize a session, and adapt its tools.

        Connection failures are logged and yield an empty tool list so one
        misconfigured connector never breaks the whole run.
        """
        try:
            if connector.transport == "sse":
                read, write = await self._exit_stack.enter_async_context(
                    sse_client(connector.url)
                )
            elif connector.transport == "stdio":
                read, write = await self._exit_stack.enter_async_context(
                    stdio_client(self._stdio_params(connector))
                )
            else:
                logger.warning(
                    "mcp_unknown_transport connector=%s transport=%s",
                    connector.name,
                    connector.transport,
                )
                return []
            session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            tools_result = await session.list_tools()
        except Exception as exc:
            self.last_error = str(exc) or type(exc).__name__
            logger.warning(
                "mcp_connect_failed connector=%s error=%s",
                connector.name,
                self.last_error,
                exc_info=True,
            )
            return []
        logger.info(
            "mcp_connected connector=%s tools=%d",
            connector.name,
            len(tools_result.tools),
        )
        return [
            McpToolAdapter(session, connector.name, tool)
            for tool in tools_result.tools
        ]

    async def close(self) -> None:
        """Tear down every context entered during connect (best effort)."""
        try:
            await self._exit_stack.aclose()
        except Exception:
            logger.warning("mcp_close_error", exc_info=True)

    @staticmethod
    def _stdio_params(connector: McpConnector) -> StdioServerParameters:
        command = connector.command
        if os.name == "nt" and command in _WINDOWS_CMD_SHIMS:
            command = f"{command}.cmd"
        # Merge os.environ so uvx/npx can find PATH and friends; connector env
        # wins on conflicts.
        env = {**os.environ, **connector.env}
        return StdioServerParameters(command=command, args=connector.args, env=env)


async def load_mcp_tools(
    connectors: list[McpConnector],
) -> tuple[list[Tool], list[McpRunConnection]]:
    """Connect every enabled connector and collect tools + connections."""
    tools: list[Tool] = []
    connections: list[McpRunConnection] = []
    for connector in connectors:
        if not connector.enabled:
            continue
        connection = McpRunConnection()
        connections.append(connection)
        tools.extend(await connection.connect(connector))
    return tools, connections
