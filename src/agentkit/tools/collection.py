from typing import Any

from agentkit.tools.base import Tool


class ToolCollection:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self.tool_map: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        self.tool_map[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tool_map.get(name)

    def require(self, name: str) -> Tool:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Tool not registered: {name}")
        return tool

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self.tool_map.values()]
