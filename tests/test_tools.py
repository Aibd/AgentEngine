from __future__ import annotations

import pytest

from agent_core.tools.base import Tool
from agent_core.tools.builtin.planning_tool import PlanningTool
from agent_core.tools.collection import ToolCollection
from agent_core.tools.registry import create_tool, register_tool, registered_tools


class _EchoTool(Tool):
    name = "echo"
    description = "Repeats the input"
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }

    async def run(self, **kwargs) -> str:
        return kwargs.get("text", "no input")


class TestTool:
    async def test_run(self):
        tool = _EchoTool()
        assert await tool.run(text="hello") == "hello"

    def test_to_openai_tool(self):
        schema = _EchoTool().to_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"
        assert "description" in schema["function"]


class TestToolCollection:
    def test_add_and_get(self):
        coll = ToolCollection()
        coll.add(_EchoTool())
        assert coll.get("echo") is not None
        assert coll.get("nope") is None

    def test_require_raises(self):
        coll = ToolCollection()
        with pytest.raises(KeyError):
            coll.require("missing")

    def test_to_openai_tools(self):
        coll = ToolCollection([_EchoTool()])
        tools = coll.to_openai_tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "echo"


class TestRegistry:
    def test_register_and_create(self):
        @register_tool("reg_echo")
        class _RegTool(Tool):
            description = "registered echo"

            async def run(self, **kwargs):
                return "ok"

        assert "reg_echo" in registered_tools()
        tool = create_tool("reg_echo")
        assert tool.name == "reg_echo"

    def test_create_unknown_raises(self):
        with pytest.raises(KeyError):
            create_tool("does_not_exist")

    def test_registered_tools_returns_copy(self):
        tools = registered_tools()
        tools.clear()
        assert registered_tools()


class TestPlanningTool:
    async def test_create_plan(self):
        tool = PlanningTool()
        result = await tool.run(action="create", steps=["a", "b"])
        plan = result["plan"]
        assert plan["steps"] == ["a", "b"]
        assert plan["currentStep"] == "a"
        assert not plan["isFinished"]

    async def test_advance_and_inspect(self):
        tool = PlanningTool()
        await tool.run(action="create", steps=["a", "b"])
        await tool.run(action="advance")
        result = await tool.run(action="inspect")
        plan = result["plan"]
        assert plan["status"] == ["completed", "pending"]
        assert plan["currentStep"] == "b"

    async def test_finish_all(self):
        tool = PlanningTool()
        await tool.run(action="create", steps=["a", "b"])
        await tool.run(action="advance")
        await tool.run(action="advance")
        result = await tool.run(action="inspect")
        assert result["plan"]["isFinished"]

    async def test_inspect_no_plan(self):
        tool = PlanningTool()
        result = await tool.run(action="inspect")
        assert result["plan"] is None

    async def test_advance_without_plan(self):
        tool = PlanningTool()
        result = await tool.run(action="advance")
        assert "error" in result
