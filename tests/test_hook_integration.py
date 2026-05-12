"""Integration tests: TurnRunner + run_turn dispatching hooks at the right places."""

from __future__ import annotations

from typing import Any

import pytest

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.hooks import (
    AfterTurnPayload,
    HookEvent,
    HookManager,
    HookResult,
    PreToolUsePayload,
    SessionStartPayload,
    StopPayload,
    UserPromptSubmitPayload,
)
from agentengine.llm.client import LLMResponse
from agentengine.runtime.events import (
    RunCompleted,
    RunFailed,
    RuntimeEvent,
    ToolCallFailed,
)
from agentengine.runtime.turn_runner import TurnRunner
from agentengine.run_config import RunConfig
from agentengine.tools.base import Tool


class _RecordingHook:
    """Captures every payload it sees + lets tests force an abort."""

    def __init__(self, *, abort_reason: str | None = None) -> None:
        self.payloads: list[Any] = []
        self.abort_reason = abort_reason

    async def __call__(self, payload: Any) -> HookResult:
        self.payloads.append(payload)
        if self.abort_reason is not None:
            return HookResult.fail_abort(self.abort_reason)
        return HookResult.success()


async def _ok_turn(agent: AgentRun, context: AgentContext, query: str) -> str:
    return f"ok:{query}"


async def _run_with_manager(
    manager: HookManager,
    *,
    turn_fn: Any = _ok_turn,
) -> tuple[list[RuntimeEvent], AgentContext, str | None]:
    context = AgentContext(request_id="req-1", query="hello")
    agent = AgentRun(config=RunConfig(name="hook_test"), context=context)
    events: list[RuntimeEvent] = []

    async def on_event(event: RuntimeEvent) -> None:
        events.append(event)

    runner = TurnRunner("session-1", hook_manager=manager)
    try:
        result = await runner.run(
            agent=agent,
            context=context,
            query="hello",
            on_event=on_event,
            turn_fn=turn_fn,
        )
    except Exception:
        return events, context, None
    return events, context, result


class TestSessionStartAndStop:
    async def test_session_start_fires_with_payload(self) -> None:
        manager = HookManager()
        capture = _RecordingHook()
        manager.register(HookEvent.SESSION_START, capture)
        events, _, result = await _run_with_manager(manager)
        assert result == "ok:hello"
        assert len(capture.payloads) == 1
        payload = capture.payloads[0]
        assert isinstance(payload, SessionStartPayload)
        assert payload.agent_name == "hook_test"
        assert payload.query_summary.startswith("hello")
        assert payload.session_id == "session-1"

    async def test_stop_fires_on_success(self) -> None:
        manager = HookManager()
        stop = _RecordingHook()
        manager.register(HookEvent.STOP, stop)
        await _run_with_manager(manager)
        assert len(stop.payloads) == 1
        assert isinstance(stop.payloads[0], StopPayload)
        assert stop.payloads[0].status == "completed"

    async def test_stop_fires_on_failure(self) -> None:
        manager = HookManager()
        stop = _RecordingHook()
        manager.register(HookEvent.STOP, stop)

        async def boom(agent, ctx, q):  # type: ignore[no-untyped-def]
            raise RuntimeError("oops")

        events, _, result = await _run_with_manager(manager, turn_fn=boom)
        assert result is None
        assert len(stop.payloads) == 1
        assert stop.payloads[0].status == "failed"

    async def test_session_start_abort_emits_run_failed_and_stop(self) -> None:
        manager = HookManager()
        manager.register(
            HookEvent.SESSION_START,
            _RecordingHook(abort_reason="tenant blocked"),
            name="gate",
        )
        stop = _RecordingHook()
        manager.register(HookEvent.STOP, stop)

        events, _, result = await _run_with_manager(manager)
        assert result is None
        # We expect a RunFailed + Stop hook fired with status=failed
        assert any(isinstance(e, RunFailed) for e in events)
        run_failed = next(e for e in events if isinstance(e, RunFailed))
        assert run_failed.error_type == "HookAbortError"
        assert "tenant blocked" in run_failed.error_message
        assert len(stop.payloads) == 1
        assert stop.payloads[0].status == "failed"


class TestUserPromptSubmit:
    async def test_user_prompt_submit_fires_with_query(self) -> None:
        # Need a real run_turn loop for UserPromptSubmit to fire — use a
        # mock LLM that immediately returns a final answer.
        manager = HookManager()
        capture = _RecordingHook()
        manager.register(HookEvent.USER_PROMPT_SUBMIT, capture)

        from mock_llm import MockLLMClient

        llm = MockLLMClient()
        llm.enqueue(LLMResponse(content="done", finish_reason="stop"))

        context = AgentContext(request_id="req-1", query="hello", llm=llm)
        agent = AgentRun(config=RunConfig(name="hook_test"), context=context)
        events: list[RuntimeEvent] = []
        runner = TurnRunner("session-1", hook_manager=manager)
        await runner.run(
            agent=agent,
            context=context,
            query="please summarize",
            on_event=events.append,
        )
        assert len(capture.payloads) == 1
        payload = capture.payloads[0]
        assert isinstance(payload, UserPromptSubmitPayload)
        assert payload.query == "please summarize"

    async def test_user_prompt_submit_abort_kills_run(self) -> None:
        manager = HookManager()
        manager.register(
            HookEvent.USER_PROMPT_SUBMIT,
            _RecordingHook(abort_reason="prompt rejected"),
            name="filter",
        )
        stop = _RecordingHook()
        manager.register(HookEvent.STOP, stop)

        from mock_llm import MockLLMClient

        llm = MockLLMClient()
        llm.enqueue(LLMResponse(content="done", finish_reason="stop"))

        context = AgentContext(request_id="req-1", query="hello", llm=llm)
        agent = AgentRun(config=RunConfig(name="hook_test"), context=context)
        events: list[RuntimeEvent] = []
        runner = TurnRunner("session-1", hook_manager=manager)
        with pytest.raises(Exception):
            await runner.run(
                agent=agent,
                context=context,
                query="please summarize",
                on_event=events.append,
            )
        assert any(isinstance(e, RunFailed) for e in events)
        assert len(stop.payloads) == 1
        assert stop.payloads[0].status == "failed"


class _ToyTool(Tool):
    name = "toy"
    description = "test fixture"
    schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> str:
        return "toy ran"


class TestPreAndPostToolUse:
    async def test_pre_post_tool_use_fire_around_execution(self) -> None:
        from mock_llm import MockLLMClient
        from agentengine.tools.collection import ToolCollection

        manager = HookManager()
        pre = _RecordingHook()
        post = _RecordingHook()
        manager.register(HookEvent.PRE_TOOL_USE, pre)
        manager.register(HookEvent.POST_TOOL_USE, post)

        llm = MockLLMClient()
        llm.enqueue(
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {"name": "toy", "arguments": "{}"},
                    }
                ],
            )
        )
        llm.enqueue(LLMResponse(content="done", finish_reason="stop"))

        coll = ToolCollection([_ToyTool()])
        context = AgentContext(
            request_id="req-1", query="hello", llm=llm, tool_collection=coll
        )
        agent = AgentRun(config=RunConfig(name="hook_test"), context=context)
        events: list[RuntimeEvent] = []
        runner = TurnRunner("session-1", hook_manager=manager)
        await runner.run(
            agent=agent,
            context=context,
            query="invoke the toy",
            on_event=events.append,
        )

        assert len(pre.payloads) == 1
        assert isinstance(pre.payloads[0], PreToolUsePayload)
        assert pre.payloads[0].tool_name == "toy"
        assert pre.payloads[0].tool_call_id == "tc_1"

        assert len(post.payloads) == 1
        assert post.payloads[0].tool_name == "toy"
        assert post.payloads[0].ok is True
        assert "toy ran" in post.payloads[0].result_summary

    async def test_pre_tool_use_abort_skips_tool(self) -> None:
        from mock_llm import MockLLMClient
        from agentengine.tools.collection import ToolCollection

        manager = HookManager()
        manager.register(
            HookEvent.PRE_TOOL_USE,
            _RecordingHook(abort_reason="not allowed"),
            name="gate",
        )
        post = _RecordingHook()
        manager.register(HookEvent.POST_TOOL_USE, post)

        llm = MockLLMClient()
        llm.enqueue(
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {"name": "toy", "arguments": "{}"},
                    }
                ],
            )
        )
        llm.enqueue(LLMResponse(content="bailed", finish_reason="stop"))

        coll = ToolCollection([_ToyTool()])
        context = AgentContext(
            request_id="req-1", query="hello", llm=llm, tool_collection=coll
        )
        agent = AgentRun(config=RunConfig(name="hook_test"), context=context)
        events: list[RuntimeEvent] = []
        runner = TurnRunner("session-1", hook_manager=manager)
        await runner.run(
            agent=agent,
            context=context,
            query="invoke the toy",
            on_event=events.append,
        )

        # The aborted tool should produce a ToolCallFailed event with our reason
        failures = [e for e in events if isinstance(e, ToolCallFailed)]
        assert len(failures) == 1
        assert failures[0].error_type == "HookAbortError"
        assert "not allowed" in failures[0].error_message
        # PostToolUse never fires when PreToolUse aborts (tool didn't run)
        assert len(post.payloads) == 0
        # Run still completes (the model gets the abort reason in memory and
        # decides what to do) - we expect RunCompleted, not RunFailed.
        assert any(isinstance(e, RunCompleted) for e in events)


class TestAfterTurn:
    async def test_after_turn_stop_ends_loop_normally(self) -> None:
        from mock_llm import MockLLMClient
        from agentengine.tools.collection import ToolCollection

        manager = HookManager()
        capture = _RecordingHook()
        manager.register(HookEvent.AFTER_TURN, capture)

        async def stop(payload: AfterTurnPayload) -> HookResult:
            if payload.turn >= 1:
                return HookResult.stop("enough")
            return HookResult.success()

        manager.register(HookEvent.AFTER_TURN, stop)

        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {"name": "toy", "arguments": "{}"},
                    }
                ],
            ),
            LLMResponse(
                content="should not be called",
                finish_reason="stop",
            ),
        ])
        context = AgentContext(
            request_id="req-1",
            query="hello",
            llm=llm,
            tool_collection=ToolCollection([_ToyTool()]),
        )
        agent = AgentRun(config=RunConfig(name="hook_test"), context=context)
        events: list[RuntimeEvent] = []

        await TurnRunner("session-1", hook_manager=manager).run(
            agent=agent,
            context=context,
            query="invoke the toy",
            on_event=events.append,
        )

        assert len(llm.calls) == 1
        assert len(capture.payloads) == 1
        assert isinstance(capture.payloads[0], AfterTurnPayload)
        assert capture.payloads[0].has_tool_calls is True
        completed = next(e for e in events if isinstance(e, RunCompleted))
        assert completed.terminal_reason == "hook_stopped"
