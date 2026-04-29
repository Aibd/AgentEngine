from __future__ import annotations

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.handlers.react import ReActHandler
from agent_core.llm.client import LLMResponse
from mock_llm import MockLLMClient
from agent_core.memory.message import Role
from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer
from agent_core.tools.builtin.planning_tool import PlanningTool
from agent_core.tools.collection import ToolCollection


class _SimpleAgent(BaseAgent):
    name = "simple"

    def system_prompt(self) -> str:
        return "You are a helpful assistant."


class _ResearchLikeAgent(BaseAgent):
    name = "researchy"

    def setup(self) -> None:
        if self.context.tool_collection.get("planning_tool") is None:
            self.context.tool_collection.add(PlanningTool())

    def system_prompt(self) -> str:
        return "Plan first, then execute."


def _make_context(llm: MockLLMClient, *, with_tools: bool = False) -> tuple[AgentContext, EventStream]:
    stream = EventStream()
    printer = Printer("req-1", stream, conversation_id="conv-1")
    tools = ToolCollection()
    if with_tools:
        tools.add(PlanningTool())
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
        assert "tool_thought" in types
        assert "result" in types
        assert events[-1]["finished"] is True

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
                            "name": "planning_tool",
                            "arguments": '{"action": "create", "steps": ["research", "write"]}',
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
        assert assistant_msgs[0].tool_calls[0]["function"]["name"] == "planning_tool"

        events = await _drain(stream)
        types = [e["responseType"] for e in events]
        assert "tool_result" in types

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

    async def test_max_steps_terminates_loop(self):
        # LLM keeps requesting tool calls; max_steps caps the loop.
        looping = LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "planning_tool", "arguments": '{"action": "inspect"}'},
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
        context, _ = _make_context(llm)  # no planning tool initially
        agent = _ResearchLikeAgent(context, max_steps=3)

        assert context.tool_collection.get("planning_tool") is None

        handler = ReActHandler()
        await handler.handle(agent, context, "hi")

        # setup() should have registered planning_tool
        assert context.tool_collection.get("planning_tool") is not None
