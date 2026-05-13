"""Tests for the streaming protocol upgrade (Phase 5).

These verify TurnRunner fans RuntimeEvent objects into the SSE sink sequence
that renderers depend on:

  start -> step -> [thinking ...] -> [text ...] -> [tool_call_start ->
  tool_result] (loop) -> step_end -> usage -> done

The point of these tests is that the renderers (terminal + React) can rely
on this ordering — regressions here will break the user-facing UI.
"""

from __future__ import annotations

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.llm.interfaces import LLMResponse
from agentengine.runtime.events import (
    ReasoningDelta,
    ToolCallCompleted,
    ToolCallStarted,
    TurnEnded,
    TurnStarted,
    UsageReport,
)
from agentengine.runtime.turn_runner import TurnRunner
from agentengine.stream.printer import Printer
from agentengine.stream.sse_queue import SseEventQueue
from agentengine.stream.sse_sink import SseSink
from agentengine.tools.base import Tool
from agentengine.tools.collection import ToolCollection
from examples.agents.general_chat.preset import PRESET as GENERAL_CHAT_PRESET
from mock_llm import MockLLMClient


def _make_agent(context: AgentContext) -> AgentRun:
    config = GENERAL_CHAT_PRESET.to_run_config()
    return AgentRun(config=config, context=context)


class _StaticTool(Tool):
    name = "echo"
    description = "Echoes input back"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, **kwargs):
        return f"echoed: {kwargs.get('text', '')}"


async def _drain(stream: SseEventQueue) -> list[dict]:
    out: list[dict] = []
    while not stream._queue.empty():
        evt = await stream._queue.get()
        if evt is not None and "comment" not in evt:
            out.append(evt)
    return out


async def _build_context(*, llm) -> tuple[AgentContext, SseEventQueue]:
    stream = SseEventQueue()
    printer = Printer(request_id="req-streaming", event_stream=stream)
    coll = ToolCollection([_StaticTool()])
    ctx = AgentContext(
        request_id="req-streaming",
        query="hello",
        llm=llm,
        printer=printer,
        tool_collection=coll,
    )
    return ctx, stream


async def _run_with_sse(agent: AgentRun, ctx: AgentContext, query: str) -> str:
    return await TurnRunner("test-session", enable_event_log=False).run(
        agent=agent,
        context=ctx,
        query=query,
        on_event=SseSink(ctx.printer).consume,
    )


class TestNoToolPath:
    """A turn without tool calls: start → step → text → step_end → usage → result."""

    async def test_event_sequence(self) -> None:
        llm = MockLLMClient([
            LLMResponse(content="hi", finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 2}),
        ])
        ctx, stream = await _build_context(llm=llm)
        agent = _make_agent(ctx)

        await _run_with_sse(agent, ctx, "hello")

        types = [e["event"] for e in await _drain(stream)]
        assert types == ["start", "step", "text", "step_end", "usage", "done"]

    async def test_usage_aggregates_tokens(self) -> None:
        llm = MockLLMClient([
            LLMResponse(content="ok", finish_reason="stop", usage={"prompt_tokens": 12, "completion_tokens": 3}),
        ])
        ctx, stream = await _build_context(llm=llm)
        agent = _make_agent(ctx)

        await _run_with_sse(agent, ctx, "hi")

        usage = next(e for e in await _drain(stream) if e["event"] == "usage")
        assert usage["data"]["prompt_tokens"] == 12
        assert usage["data"]["completion_tokens"] == 3
        assert usage["data"]["total_tokens"] == 15
        assert usage["data"]["total_seconds"] >= 0.0


class TestThinkingPath:
    """When the model emits reasoning_content, we surface it as 'thinking' events."""

    async def test_thinking_appears_before_text(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content="answer",
                reasoning_content="let me think...",
                finish_reason="stop",
            ),
        ])
        ctx, stream = await _build_context(llm=llm)
        agent = _make_agent(ctx)

        await _run_with_sse(agent, ctx, "hi")

        types = [e["event"] for e in await _drain(stream)]
        # Order matters: step → text events → step_end (thinking is interleaved
        # depending on chunk order from the LLM client; just assert it appears
        # in the right window).
        assert "thinking" in types
        assert "text" in types
        thinking_idx = types.index("thinking")
        step_end_idx = types.index("step_end")
        assert thinking_idx < step_end_idx


class TestToolCallPath:
    """Two-turn run: assistant calls a tool, then summarises the result."""

    async def test_tool_call_emits_full_lifecycle(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                }],
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ])
        ctx, stream = await _build_context(llm=llm)
        agent = _make_agent(ctx)

        await _run_with_sse(agent, ctx, "use echo")

        events = await _drain(stream)
        types = [e["event"] for e in events]

        # Lifecycle markers in order
        assert types[0] == "start"
        assert types.count("step") == 2
        assert types.count("step_end") == 2
        assert "tool_call_start" in types
        assert "tool_result" in types
        assert types[-1] == "done"

        # tool_call_start carries arguments + id
        call_start = next(e for e in events if e["event"] == "tool_call_start")
        assert call_start["data"]["tool"] == "echo"
        assert call_start["data"]["arguments"] == {"text": "hi"}
        assert call_start["data"]["tool_call_id"] == "call_1"

        # tool_result carries ok + elapsed + tool_call_id
        tool_result = next(e for e in events if e["event"] == "tool_result")
        assert tool_result["data"]["ok"] is True
        assert tool_result["data"]["tool_call_id"] == "call_1"
        assert tool_result["data"]["elapsed_ms"] >= 0

        # First step_end has has_tool_calls=True, second one is False
        step_ends = [e for e in events if e["event"] == "step_end"]
        assert step_ends[0]["data"]["has_tool_calls"] is True
        assert step_ends[1]["data"]["has_tool_calls"] is False

    async def test_unknown_tool_marks_result_as_failed(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "no_such_tool", "arguments": "{}"},
                }],
            ),
            LLMResponse(content="recovered", finish_reason="stop"),
        ])
        ctx, stream = await _build_context(llm=llm)
        agent = _make_agent(ctx)

        await _run_with_sse(agent, ctx, "use missing")

        events = await _drain(stream)
        tool_result = next(e for e in events if e["event"] == "tool_result")
        assert tool_result["data"]["ok"] is False
        assert tool_result["data"]["error_type"] == "ToolNotFound"

    async def test_multi_turn_tool_calls_emit_ordered_lifecycle_and_feedback(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content="",
                reasoning_content="Need the first lookup.",
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 1},
                tool_calls=[{
                    "id": "call_lookup",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "lookup"}'},
                }],
            ),
            LLMResponse(
                content="",
                reasoning_content="Need one refinement.",
                finish_reason="tool_calls",
                usage={"prompt_tokens": 20, "completion_tokens": 2},
                tool_calls=[{
                    "id": "call_refine",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "refine"}'},
                }],
            ),
            LLMResponse(
                content="final answer",
                finish_reason="stop",
                usage={"prompt_tokens": 30, "completion_tokens": 3},
            ),
        ])
        ctx, stream = await _build_context(llm=llm)
        agent = _make_agent(ctx)

        result = await _run_with_sse(agent, ctx, "research with tools")

        assert result == "final answer"
        assert len(llm.calls) == 3

        events = await _drain(stream)
        types = [e["event"] for e in events]
        assert types == [
            "start",
            "step",
            "thinking",
            "tool_call_start",
            "tool_result",
            "step_end",
            "step",
            "thinking",
            "tool_call_start",
            "tool_result",
            "step_end",
            "step",
            "text",
            "step_end",
            "usage",
            "done",
        ]

        step_ends = [e["data"] for e in events if e["event"] == "step_end"]
        assert [e["turn"] for e in step_ends] == [1, 2, 3]
        assert [e["has_tool_calls"] for e in step_ends] == [True, True, False]

        tool_starts = [e["data"] for e in events if e["event"] == "tool_call_start"]
        assert [(e["tool_call_id"], e["arguments"]) for e in tool_starts] == [
            ("call_lookup", {"text": "lookup"}),
            ("call_refine", {"text": "refine"}),
        ]

        tool_results = [e["data"] for e in events if e["event"] == "tool_result"]
        assert [(e["tool_call_id"], e["result"], e["ok"]) for e in tool_results] == [
            ("call_lookup", "echoed: lookup", True),
            ("call_refine", "echoed: refine", True),
        ]

        usage = next(e["data"] for e in events if e["event"] == "usage")
        assert usage["prompt_tokens"] == 60
        assert usage["completion_tokens"] == 6
        assert usage["total_tokens"] == 66

        second_messages = llm.calls[1]["messages"]
        assert [m["tool_call_id"] for m in second_messages if m["role"] == "tool"] == [
            "call_lookup",
        ]

        third_messages = llm.calls[2]["messages"]
        tool_messages = [m for m in third_messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_messages] == [
            "call_lookup",
            "call_refine",
        ]
        assert [m["content"] for m in tool_messages] == [
            "echoed: lookup",
            "echoed: refine",
        ]

        assistant_tool_turns = [
            m for m in third_messages
            if m["role"] == "assistant" and m.get("tool_calls")
        ]
        assert [m["tool_calls"][0]["id"] for m in assistant_tool_turns] == [
            "call_lookup",
            "call_refine",
        ]

        runtime_events = [
            event
            for event in ctx.extras["runtime_events"]
            if isinstance(event, (ToolCallStarted, ToolCallCompleted))
        ]
        assert [
            event.tool_call_id
            for event in runtime_events
        ] == [
            "call_lookup",
            "call_lookup",
            "call_refine",
            "call_refine",
        ]


class TestRuntimeEventBridge:
    """RuntimeEvent → SSE bridge via Printer.from_runtime_event."""

    async def test_bridges_all_new_runtime_events(self) -> None:
        stream = SseEventQueue()
        printer = Printer(request_id="req-bridge", event_stream=stream)

        await printer.from_runtime_event(
            TurnStarted(run_id="r", turn_id="t", turn=1)
        )
        await printer.from_runtime_event(
            ReasoningDelta(run_id="r", turn_id="t", content="...")
        )
        await printer.from_runtime_event(
            TurnEnded(run_id="r", turn_id="t", turn=1, has_tool_calls=False, elapsed_seconds=0.1)
        )
        await printer.from_runtime_event(
            UsageReport(
                run_id="r", turn_id="t",
                prompt_tokens=5, completion_tokens=2, total_tokens=7, total_seconds=0.5,
            )
        )

        types = [e["event"] for e in await _drain(stream)]
        assert types == ["step", "thinking", "step_end", "usage"]
