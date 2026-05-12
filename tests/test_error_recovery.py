"""Error-path integration tests.

Covers what happens end-to-end when things go wrong. The unit tests in
test_turn_runner.py / test_stream.py / test_react_handler.py cover narrow
slices; this file makes sure the slices compose correctly:

  exception inside run_turn
       → TurnRunner classifies + emits RunFailed/RunCancelled
       → Printer.from_runtime_event renders SSE error event
       → Client receives structured error_payload (not crashed connection)

Without this layer, any handler/middleware change can silently break the
"the user sees a friendly error" guarantee.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agentengine.base.context import AgentContext
from agentengine.errors import (
    LLMConnectionError,
    LLMHTTPError,
    LLMTimeoutError,
    ToolExecutionError,
)
from agentengine.llm.interfaces import LLMChunk, LLMResponse
from agentengine.runtime.events import RunCancelled, RunFailed
from agentengine.runtime.run_state import RunStatus, TerminalReason
from agentengine.tools.base import Tool
from agentengine.tools.collection import ToolCollection
from mock_llm import MockLLMClient
from examples.reference_app.services.agent_orchestration_service import AgentOrchestrationService


class _FailingLLM:
    """LLM that raises a chosen exception on first chat() / chat_stream() call."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls: list[dict] = []

    async def chat(self, messages, *, tools=None, stream=False, **kwargs) -> LLMResponse:
        self.calls.append({"stream": stream})
        raise self._exc

    async def chat_stream(self, messages, *, tools=None, **kwargs) -> AsyncIterator[LLMChunk]:
        self.calls.append({"stream": True})
        # Raise via async generator
        if False:
            yield LLMChunk()  # pragma: no cover
        raise self._exc


class _HangingLLM:
    """LLM that never returns — used for cancellation testing."""

    async def chat(self, *args, **kwargs) -> LLMResponse:
        await asyncio.Event().wait()
        return LLMResponse()  # pragma: no cover

    async def chat_stream(self, *args, **kwargs) -> AsyncIterator[LLMChunk]:
        await asyncio.Event().wait()
        if False:
            yield LLMChunk()  # pragma: no cover


class _SlowTool(Tool):
    name = "slow_tool"
    description = "Sleeps longer than the configured tool timeout"
    schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        await asyncio.sleep(5.0)
        return "should never reach here"


async def _drain(stream) -> list[dict]:
    out = []
    while not stream._queue.empty():
        evt = await stream._queue.get()
        if evt is None:
            break
        if "comment" not in evt:
            out.append(evt)
    return out


# ---------------------------------------------------------------------------
# 1. LLM HTTP error (401/403) — non-retryable, surfaces as `error` SSE
# ---------------------------------------------------------------------------

class TestLLMHttpError:
    async def test_401_invalid_key_emits_error_event(self) -> None:
        """An invalid API key surfaces as a structured error frame, not a crash."""
        llm = _FailingLLM(
            LLMHTTPError(
                "Unauthorized",
                status_code=401,
                body='{"error": "invalid_api_key"}',
                retryable=False,
            )
        )
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-401",
            query="hello",
            conversation_id="err-401",
        )
        context.llm = llm

        with pytest.raises(LLMHTTPError):
            await service.run(agent_name="general_chat", query="hello", context=context)

        events = await _drain(stream)
        types = [e["event"] for e in events]
        assert "error" in types, f"no error event in {types}"

        error_evt = next(e for e in events if e["event"] == "error")
        data = error_evt["data"]
        # Must carry structured fields the front end can render
        assert data["code"] == "llm_http_error"
        assert data["category"] == "llm"
        assert data["retryable"] is False
        assert data.get("status_code") == 401

        # Run state should reflect MODEL_FAILED (LLMError → MODEL_FAILED)
        run_state = context.extras["run_state"]
        assert run_state.status == RunStatus.FAILED
        assert run_state.terminal_reason == TerminalReason.MODEL_FAILED

        # The runtime_events list must contain a RunFailed at the tail
        runtime_events = context.extras["runtime_events"]
        assert isinstance(runtime_events[-1], RunFailed)
        assert runtime_events[-1].terminal_reason == "model_failed"


# ---------------------------------------------------------------------------
# 2. LLM connection / timeout error — non-retryable from caller's view
#    (retry middleware would handle it, but Service doesn't enable retry by default)
# ---------------------------------------------------------------------------

class TestLLMTransportError:
    async def test_timeout_propagates_as_run_failed(self) -> None:
        llm = _FailingLLM(LLMTimeoutError("upstream took too long"))
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-timeout",
            query="hello",
            conversation_id="err-timeout",
        )
        context.llm = llm

        with pytest.raises(LLMTimeoutError):
            await service.run(agent_name="general_chat", query="hello", context=context)

        events = await _drain(stream)
        error_evt = next(e for e in events if e["event"] == "error")
        assert error_evt["data"]["code"] == "llm_timeout"
        assert error_evt["data"]["retryable"] is True  # the error class itself is retryable

        run_state = context.extras["run_state"]
        assert run_state.terminal_reason == TerminalReason.MODEL_FAILED

    async def test_connection_error_propagates_as_run_failed(self) -> None:
        llm = _FailingLLM(LLMConnectionError("could not connect"))
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-conn",
            query="hello",
            conversation_id="err-conn",
        )
        context.llm = llm

        with pytest.raises(LLMConnectionError):
            await service.run(agent_name="general_chat", query="hello", context=context)

        events = await _drain(stream)
        error_evt = next(e for e in events if e["event"] == "error")
        assert error_evt["data"]["code"] == "llm_connection_error"


# ---------------------------------------------------------------------------
# 3. Tool execution timeout — single tool fails but the agent recovers
#    (LLM is asked to summarize, no RunFailed at the run level)
# ---------------------------------------------------------------------------

class TestToolTimeout:
    async def test_tool_timeout_emits_failed_tool_result(self) -> None:
        """Tool timeout produces tool_result(ok=false) and the run continues."""
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "slow_tool", "arguments": "{}"},
                }],
            ),
            LLMResponse(content="Recovered after the tool timed out.", finish_reason="stop"),
        ])
        tool_collection = ToolCollection([_SlowTool()])
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-tool-timeout",
            query="use slow tool",
            conversation_id="err-tool-timeout",
        )
        context.llm = llm
        context.tool_collection = tool_collection

        result = await service.run(
            agent_name="general_chat",
            query="use slow tool",
            context=context,
            tool_timeout_seconds=0.05,  # Short enough to definitely fire
        )

        # Run as a whole succeeded — agent recovered after the tool failure
        assert "Recovered" in result

        events = await _drain(stream)
        types = [e["event"] for e in events]
        # Must include both tool_call_start and a tool_result with ok=false
        assert "tool_call_start" in types
        tool_results = [e for e in events if e["event"] == "tool_result"]
        assert tool_results, "expected at least one tool_result event"
        assert tool_results[0]["data"]["ok"] is False
        assert "Tool timeout" in tool_results[0]["data"].get("error_message", "")
        assert tool_results[0]["data"].get("error_type") == "TimeoutError"

        # Final terminal event should still be `done`, not `error`
        assert types[-1] == "done", f"expected done, got {types[-1]}"


# ---------------------------------------------------------------------------
# 4. Cancellation — Ctrl-C / asyncio.CancelledError
# ---------------------------------------------------------------------------

class TestCancellation:
    async def test_cancelled_run_emits_run_cancelled_event(self) -> None:
        """asyncio.CancelledError must produce a RunCancelled SSE error frame."""
        llm = _HangingLLM()
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-cancel",
            query="hang forever",
            conversation_id="err-cancel",
        )
        context.llm = llm

        run_task = asyncio.create_task(
            service.run(agent_name="general_chat", query="hang forever", context=context)
        )
        # Yield control so the task starts, then cancel.
        await asyncio.sleep(0.01)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        events = await _drain(stream)
        types = [e["event"] for e in events]
        assert "error" in types, f"no error event in {types}"

        error_evt = next(e for e in events if e["event"] == "error")
        # Cancellation goes through Printer.from_runtime_event(RunCancelled)
        assert error_evt["data"]["code"] == "cancelled"
        assert error_evt["data"]["category"] == "runtime"

        # State machine reflects the cancellation
        run_state = context.extras["run_state"]
        assert run_state.status == RunStatus.CANCELLED
        runtime_events = context.extras["runtime_events"]
        assert isinstance(runtime_events[-1], RunCancelled)


# ---------------------------------------------------------------------------
# 5. Unexpected runtime error — fallback path
# ---------------------------------------------------------------------------

class TestUnexpectedError:
    async def test_unknown_exception_is_classified_as_runtime_failed(self) -> None:
        """Non-AgentEngineError exceptions still emit a structured error event."""
        llm = _FailingLLM(RuntimeError("something exploded"))
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-runtime",
            query="hi",
            conversation_id="err-runtime",
        )
        context.llm = llm

        with pytest.raises(RuntimeError):
            await service.run(agent_name="general_chat", query="hi", context=context)

        events = await _drain(stream)
        error_evt = next(e for e in events if e["event"] == "error")
        # Unmapped exceptions get a generic code via error_to_dict
        assert error_evt["data"]["code"] == "unexpected_error"
        assert "exploded" in error_evt["data"]["message"]
        assert error_evt["data"]["details"]["type"] == "RuntimeError"

        run_state = context.extras["run_state"]
        assert run_state.terminal_reason == TerminalReason.RUNTIME_FAILED


# ---------------------------------------------------------------------------
# 6. Tool failure does NOT abort the run (resilience)
# ---------------------------------------------------------------------------

class TestToolFailureResilience:
    """A failing tool must not crash the agent — only the tool call fails."""

    async def test_unknown_tool_keeps_run_alive(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "no_such_tool", "arguments": "{}"},
                }],
            ),
            LLMResponse(content="The tool didn't exist; here is a fallback.", finish_reason="stop"),
        ])
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-tool-missing",
            query="use missing",
            conversation_id="err-tool-missing",
        )
        context.llm = llm

        result = await service.run(
            agent_name="general_chat",
            query="use missing",
            context=context,
        )

        assert "fallback" in result.lower()

        events = await _drain(stream)
        tool_results = [e for e in events if e["event"] == "tool_result"]
        assert tool_results
        assert tool_results[0]["data"]["ok"] is False
        assert tool_results[0]["data"]["error_type"] == "ToolNotFound"

        types = [e["event"] for e in events]
        assert types[-1] == "done"  # not "error"


# ---------------------------------------------------------------------------
# 7. Tool raising arbitrary Python exception — also recoverable
# ---------------------------------------------------------------------------

class _ExplodingTool(Tool):
    name = "exploding_tool"
    description = "Always raises"
    schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        raise ValueError("kaboom")


class TestToolRaisesException:
    async def test_tool_exception_emits_failed_result_run_recovers(self) -> None:
        llm = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "exploding_tool", "arguments": "{}"},
                }],
            ),
            LLMResponse(content="The tool crashed; continuing.", finish_reason="stop"),
        ])
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        context, stream = service.create_streaming_context(
            request_id="err-tool-raise",
            query="boom",
            conversation_id="err-tool-raise",
        )
        context.llm = llm
        context.tool_collection = ToolCollection([_ExplodingTool()])

        result = await service.run(
            agent_name="general_chat",
            query="boom",
            context=context,
        )
        assert "crashed" in result.lower()

        events = await _drain(stream)
        tool_results = [e for e in events if e["event"] == "tool_result"]
        assert tool_results[0]["data"]["ok"] is False
        assert tool_results[0]["data"]["error_type"] == "ValueError"
        assert "kaboom" in tool_results[0]["data"]["error_message"]
