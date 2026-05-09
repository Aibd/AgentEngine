from __future__ import annotations

import asyncio
from dataclasses import replace

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.base.state import AgentState
from agentengine.runtime.turn import DEFAULT_TOOL_TIMEOUT_SECONDS, run_turn
from agentengine.llm.client import LLMResponse
from agentengine.memory.message import Role
from agentengine.runtime.turn_runner import TurnRunner
from agentengine.spec import AgentSpec
from agentengine.stream.printer import Printer
from agentengine.stream.sse_queue import SseEventQueue
from agentengine.stream.sse_sink import SseSink
from agentengine.tools.base import Tool
from agentengine.tools.collection import ToolCollection
from mock_llm import MockLLMClient


class _EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, **kwargs):
        return kwargs.get("text", "echo")


SIMPLE_SPEC = AgentSpec(
    name="simple",
    system_prompt="You are a helpful assistant.",
)


async def _setup_research(context: AgentContext) -> None:
    if context.tool_collection and context.tool_collection.get("echo") is None:
        context.tool_collection.add(_EchoTool())


RESEARCHY_SPEC = AgentSpec(
    name="researchy",
    system_prompt="Plan first, then execute.",
    setup=_setup_research,
)


def _agent(spec: AgentSpec, context: AgentContext, *, max_steps: int | None = None) -> AgentRun:
    if max_steps is not None and max_steps != spec.max_steps:
        spec = replace(spec, max_steps=max_steps)
    return AgentRun(spec=spec, context=context)


class _SlowTool(Tool):
    name = "slow_tool"
    description = "Sleeps longer than the test timeout"
    schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        await asyncio.sleep(1)
        return "too late"


def _make_context(llm: MockLLMClient, *, with_tools: bool = False) -> tuple[AgentContext, SseEventQueue]:
    stream = SseEventQueue()
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


async def _drain(stream: SseEventQueue) -> list[dict]:
    events: list[dict] = []
    while not stream._queue.empty():
        e = await stream._queue.get()
        if e is None:
            break
        if "comment" in e:
            continue
        events.append(e)
    return events


async def _run_with_sse(
    agent: AgentRun,
    context: AgentContext,
    query: str,
    *,
    tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> str:
    return await TurnRunner("test-session", enable_event_log=False).run(
        agent=agent,
        context=context,
        query=query,
        on_event=SseSink(context.printer).consume,
        tool_timeout_seconds=tool_timeout_seconds,
    )


class TestRunTurn:
    async def test_single_turn_completes(self):
        llm = MockLLMClient([LLMResponse(content="The answer is 4.", finish_reason="stop")])
        context, stream = _make_context(llm)
        agent = _agent(SIMPLE_SPEC, context, max_steps=5)

        result = await _run_with_sse(agent, context, "What is 2+2?")

        assert result == "The answer is 4."
        assert agent.state == AgentState.FINISHED

        events = await _drain(stream)
        types = [e["event"] for e in events]
        assert "start" in types
        assert "text" in types
        assert "done" in types
        assert llm.calls[0]["stream"] is True

    async def test_system_prompt_injected(self):
        llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
        context, _ = _make_context(llm)
        agent = _agent(SIMPLE_SPEC, context, max_steps=3)

        await run_turn(agent, context, "hi")

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
        agent = _agent(RESEARCHY_SPEC, context, max_steps=5)

        result = await _run_with_sse(agent, context, "make a plan")

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
        types = [e["event"] for e in events]
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
        agent = _agent(SIMPLE_SPEC, context, max_steps=5)

        await run_turn(agent, context, "use missing tool")

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
        agent = _agent(SIMPLE_SPEC, context, max_steps=5)

        result = await run_turn(agent, context, "use slow tool", tool_timeout_seconds=0.01)

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
        agent = _agent(RESEARCHY_SPEC, context, max_steps=2)

        await run_turn(agent, context, "loop forever")

        assert agent.current_step == 2
        assert len(llm.calls) == 2

    async def test_setup_hook_runs_before_loop(self):
        llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
        context, _ = _make_context(llm)  # no echo tool initially
        agent = _agent(RESEARCHY_SPEC, context, max_steps=3)

        assert context.tool_collection.get("echo") is None

        await run_turn(agent, context, "hi")

        # setup() should have registered echo tool
        assert context.tool_collection.get("echo") is not None

    async def test_invalid_max_steps_raises(self):
        try:
            AgentSpec(name="bad", max_steps=0)
        except ValueError as exc:
            assert "max_steps" in str(exc)
        else:
            raise AssertionError("expected ValueError")
