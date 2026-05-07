from __future__ import annotations

import asyncio

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.handlers.react import ReActHandler
from agent_core.llm.client import LLMResponse
from mock_llm import MockLLMClient
from agent_core.memory.message import Role
from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer
from agent_core.tools.base import Tool
from agent_core.tools.collection import ToolCollection


class _EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, **kwargs):
        return kwargs.get("text", "echo")


class _SimpleAgent(BaseAgent):
    name = "simple"

    def system_prompt(self) -> str:
        return "You are a helpful assistant."


class _ResearchLikeAgent(BaseAgent):
    name = "researchy"

    def setup(self) -> None:
        if self.context.tool_collection.get("echo") is None:
            self.context.tool_collection.add(_EchoTool())

    def system_prompt(self) -> str:
        return "Plan first, then execute."


class _SlowTool(Tool):
    name = "slow_tool"
    description = "Sleeps longer than the test timeout"
    schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        await asyncio.sleep(1)
        return "too late"


def _make_context(llm: MockLLMClient, *, with_tools: bool = False) -> tuple[AgentContext, EventStream]:
    stream = EventStream()
    printer = Printer("req-1", stream, conversation_id="conv-1")
    tools = ToolCollection()
    if with_tools:
        tools.add(_EchoTool())
    context = AgentContext(
        request_id="req-1",
        query="test",
        llm=llm,
        printer=printer,
        tool_collection=tools,
        conversation_id="conv-1",
    )
    return context, stream


async def _drain(stream: EventStream) -> list[dict]:
    events: list[dict] = []
    while not stream._queue.empty():
        e = await stream._queue.get()
        if e is None:
            break
        events.append(e)
    return events


class TestReActHandler:
    async def test_single_turn_completes(self):
        llm = MockLLMClient([LLMResponse(content="The answer is 4.", finish_reason="stop")])
        context, stream = _make_context(llm)
        agent = _SimpleAgent(context, max_steps=5)

        handler = ReActHandler()
        result = await handler.handle(agent, context, "What is 2+2?")

        assert result == "The answer is 4."
        assert agent.state == AgentState.FINISHED

        events = await _drain(stream)
        types = [e["responseType"] for e in events]
        assert "start" in types
        assert "text" in types
        assert "result" in types
        assert events[-1]["finished"] is True
        assert llm.calls[0]["stream"] is True

    async def test_system_prompt_injected(self):
        llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
        context, _ = _make_context(llm)
        agent = _SimpleAgent(context, max_steps=3)

        handler = ReActHandler()
        await handler.handle(agent, context, "hi")

        # First two messages in memory should be system + user
        assert agent.memory.messages[0].role == Role.SYSTEM
        assert agent.memory.messages[0].content == "You are a helpful assistant."
        assert agent.memory.messages[1].role == Role.USER

    async def test_tool_call_loop(self):
        llm = MockLLMClient([
            LLMResponse(
                content="",
                reasoning_content="I should create a plan first.",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": '{"text": "research"}',
                        },
                    }
                ],
            ),
            LLMResponse(content="Plan created with 2 steps.", finish_reason="stop"),
        ])
        context, stream = _make_context(llm, with_tools=True)
        agent = _ResearchLikeAgent(context, max_steps=5)

        handler = ReActHandler()
        result = await handler.handle(agent, context, "make a plan")

        assert "plan" in result.lower()
        assert agent.state == AgentState.FINISHED
        assert len(llm.calls) == 2

        # Second LLM call should include the tool message in its messages
        second_messages = llm.calls[1]["messages"]
        tool_messages = [m for m in second_messages if m["role"] == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_call_id"] == "c1"

        assistant_turns = [m for m in second_messages if m["role"] == "assistant"]
        assert assistant_turns[0]["reasoning_content"] == "I should create a plan first."

        # Memory should record assistant message with tool_calls preserved
        assistant_msgs = [m for m in agent.memory.messages if m.role == Role.ASSISTANT]
        assert assistant_msgs[0].tool_calls is not None
        assert assistant_msgs[0].tool_calls[0]["function"]["name"] == "echo"

        events = await _drain(stream)
        types = [e["responseType"] for e in events]
        assert "tool_result" in types
        assert llm.calls[0]["stream"] is True
        assert llm.calls[1]["stream"] is True

    async def test_unknown_tool_recovers_gracefully(self):
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "nonexistent", "arguments": "{}"},
                    }
                ],
            ),
            LLMResponse(content="I'll skip that tool.", finish_reason="stop"),
        ])
        context, _ = _make_context(llm)
        agent = _SimpleAgent(context, max_steps=5)

        handler = ReActHandler()
        result = await handler.handle(agent, context, "use missing tool")

        assert agent.state == AgentState.FINISHED
        # Tool message should record the "Unknown tool" string
        tool_msgs = [m for m in agent.memory.messages if m.role == Role.TOOL]
        assert tool_msgs and "Unknown tool" in tool_msgs[0].content

    async def test_tool_timeout_is_recorded(self):
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "slow_tool", "arguments": "{}"},
                    }
                ],
            ),
            LLMResponse(content="Recovered after timeout.", finish_reason="stop"),
        ])
        context, _ = _make_context(llm)
        context.tool_collection.add(_SlowTool())
        agent = _SimpleAgent(context, max_steps=5)

        handler = ReActHandler(tool_timeout_seconds=0.01)
        result = await handler.handle(agent, context, "use slow tool")

        assert result == "Recovered after timeout."
        tool_msgs = [m for m in agent.memory.messages if m.role == Role.TOOL]
        assert tool_msgs and "Tool timeout after" in tool_msgs[0].content

    async def test_max_steps_terminates_loop(self):
        # LLM keeps requesting tool calls; max_steps caps the loop.
        looping = LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "loop"}'},
                }
            ],
        )
        llm = MockLLMClient([looping] * 10)
        context, _ = _make_context(llm, with_tools=True)
        agent = _ResearchLikeAgent(context, max_steps=2)

        handler = ReActHandler()
        await handler.handle(agent, context, "loop forever")

        assert agent.current_step == 2
        assert len(llm.calls) == 2

    async def test_setup_hook_runs_before_loop(self):
        llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
        context, _ = _make_context(llm)  # no echo tool initially
        agent = _ResearchLikeAgent(context, max_steps=3)

        assert context.tool_collection.get("echo") is None

        handler = ReActHandler()
        await handler.handle(agent, context, "hi")

        # setup() should have registered echo tool
        assert context.tool_collection.get("echo") is not None

    async def test_invalid_max_steps_raises(self):
        llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
        context, _ = _make_context(llm)

        try:
            _SimpleAgent(context, max_steps=0)
        except ValueError as exc:
            assert "max_steps" in str(exc)
        else:
            raise AssertionError("expected ValueError")
