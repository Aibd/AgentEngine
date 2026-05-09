"""End-to-end tests against a real LLM provider.

All tests in this file are skipped unless `RUN_INTEGRATION=1`. They validate:
  - 真实 streaming (no mocks anywhere in the chain)
  - SSE event sequence matches the v2 contract
  - run_id / turn_id propagate to event payloads
  - JSONL run log is written to disk
  - Tool call lifecycle works with real tool dispatching

These are the "smoke" suite — keep them small in count so RUN_INTEGRATION=1
remains cheap to run before commits.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentkit.llm.factory import create_llm_from_env
from services.agent_orchestration_service import AgentOrchestrationService


pytestmark = pytest.mark.integration

skip_unless_integration = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to call the real LLM provider.",
)


@skip_unless_integration
async def test_deepseek_general_chat_smoke() -> None:
    """One-shot reply: verifies streaming + final result reach the caller."""
    llm = create_llm_from_env()
    assert llm is not None

    try:
        result = await AgentOrchestrationService(
            llm_factory=lambda: llm,
        ).run(
            agent_name="general_chat",
            query="Reply with exactly: DeepSeek integration test ok.",
        )
    finally:
        await llm.close()

    assert result.strip()


@skip_unless_integration
async def test_general_chat_emits_v2_sse_sequence() -> None:
    """SSE event order must match the v2 contract used by the front end.

    This catches regressions in the runtime → Printer pipeline that mock-only
    tests can't see (e.g. emit fan-out wiring, run/turn id propagation).
    """
    llm = create_llm_from_env()
    assert llm is not None

    service = AgentOrchestrationService(
        llm_factory=lambda: llm,
    )
    context, stream = service.create_streaming_context(
        request_id="e2e-sse",
        query="hi",
        conversation_id="e2e-conv",
    )
    context.llm = llm

    try:
        await service.run(agent_name="general_chat", query="say hi", context=context)
    finally:
        await llm.close()

    events = []
    while not stream._queue.empty():
        evt = await stream._queue.get()
        if evt is None:
            break
        if "comment" in evt:
            continue
        events.append(evt)

    types = [e["event"] for e in events]
    # The exact prefix is fixed; usage / done close the run regardless of length
    assert types[0] == "start"
    assert types[1] == "step"
    assert "text" in types
    assert types[-2:] == ["usage", "done"]

    # Every event must carry run_id / turn_id (the front-end groups by these)
    for evt in events:
        data = evt["data"]
        assert data.get("run_id"), f"event {evt['event']} missing run_id"
        assert data.get("turn_id"), f"event {evt['event']} missing turn_id"


@skip_unless_integration
async def test_deep_research_invokes_read_file_tool(tmp_path: Path) -> None:
    """deep_research with a real tool call: model must read a file and report back.

    Validates:
      - ToolCollection registration in spec.setup actually runs
      - tool_call_start / tool_result event pair fires
      - Memory contains the assistant tool_call + tool message round-trip
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "NOTE.md").write_text(
        "# Project Note\n\nThis is a fixture file used by the e2e test.\n",
        encoding="utf-8",
    )

    os.environ["AGENT_WORKSPACE_ROOT"] = str(workspace)

    llm = create_llm_from_env()
    assert llm is not None

    service = AgentOrchestrationService(
        llm_factory=lambda: llm,
    )
    context, stream = service.create_streaming_context(
        request_id="e2e-tools",
        query="read NOTE.md and summarise in one line",
        conversation_id="e2e-tools",
    )
    context.llm = llm

    try:
        result = await service.run(
            agent_name="deep_research",
            query="读 NOTE.md 后用一句话总结这个文件的用途",
            context=context,
        )
    finally:
        await llm.close()

    assert result.strip()

    events = []
    while not stream._queue.empty():
        evt = await stream._queue.get()
        if evt is None:
            break
        if "comment" not in evt:
            events.append(evt)
    types = [e["event"] for e in events]
    assert "tool_call_start" in types, f"no tool_call_start in {types}"
    assert "tool_result" in types, f"no tool_result in {types}"

    memory = context.extras["agent_memory"]
    tool_messages = [m for m in memory if m["role"] == "tool"]
    assert tool_messages, "expected at least one tool result message in memory"
    assert any("Project Note" in (m.get("content") or "") for m in tool_messages), \
        "tool result did not include the file contents"


@skip_unless_integration
async def test_conversation_history_persists_across_runs(tmp_path: Path) -> None:
    """Two separate service instances sharing one SQLite DB must share history.

    Mirrors the real chatbot flow: user sends msg, server saves it, refreshes,
    user sends a follow-up referring to the prior turn — the model must see
    both turns in its prompt without the caller passing them explicitly.
    """
    from agentkit.base.context import AgentContext
    from agentkit.persistence import SqlitePersistence

    store = SqlitePersistence(tmp_path / "chat.db")

    llm_one = create_llm_from_env()
    assert llm_one is not None
    service_one = AgentOrchestrationService(
        llm_factory=lambda: llm_one,
        persistence=store,
    )
    ctx_one = AgentContext(
        request_id="e2e-persist-1",
        query="My name is Aibd. Reply with just: Hi Aibd.",
        conversation_id="e2e-conv-persist",
    )
    try:
        await service_one.run(
            agent_name="general_chat",
            query="My name is Aibd. Reply with just: Hi Aibd.",
            context=ctx_one,
        )
    finally:
        await llm_one.close()

    # Verify the first turn is in the DB
    stored = await store.load_messages("e2e-conv-persist")
    assert any("Aibd" in (m.get("content") or "") for m in stored)

    # Second service: fresh process, only the DB is shared.
    llm_two = create_llm_from_env()
    assert llm_two is not None
    service_two = AgentOrchestrationService(
        llm_factory=lambda: llm_two,
        persistence=store,
    )
    ctx_two = AgentContext(
        request_id="e2e-persist-2",
        query="What's my name?",
        conversation_id="e2e-conv-persist",
    )
    try:
        result = await service_two.run(
            agent_name="general_chat",
            query="What's my name?",
            context=ctx_two,
        )
    finally:
        await llm_two.close()

    # The reply should reference the name from the first turn — proving the
    # second LLM saw the prior conversation.
    assert "Aibd" in result, f"history not replayed; got: {result!r}"


@skip_unless_integration
async def test_invalid_api_key_surfaces_as_error_event() -> None:
    """Real LLM call with a bad key must reach the client as an `error` SSE frame.

    This catches a class of bug where an exception escapes the runtime event
    bridge and the client sees a closed connection instead of a structured
    error payload.
    """
    from agentkit.llm.openai_compat import OpenAICompatibleClient
    from agentkit.errors import LLMHTTPError

    bad_llm = OpenAICompatibleClient(
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        api_key="sk-deliberately-invalid-key-for-test",
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
    )
    service = AgentOrchestrationService(
        llm_factory=lambda: bad_llm,
    )
    context, stream = service.create_streaming_context(
        request_id="e2e-bad-key",
        query="hi",
        conversation_id="e2e-bad-key",
    )
    context.llm = bad_llm

    try:
        with pytest.raises(LLMHTTPError):
            await service.run(
                agent_name="general_chat",
                query="hi",
                context=context,
            )
    finally:
        await bad_llm.close()

    events = []
    while not stream._queue.empty():
        evt = await stream._queue.get()
        if evt is None:
            break
        if "comment" not in evt:
            events.append(evt)

    types = [e["event"] for e in events]
    assert "error" in types, f"no error event in {types}"
    error_evt = next(e for e in events if e["event"] == "error")
    # DeepSeek returns 401; the runtime preserves the structured payload
    assert error_evt["data"]["category"] == "llm"
    assert error_evt["data"].get("status_code") in (401, 403)


@skip_unless_integration
async def test_jsonl_run_log_is_written(tmp_path: Path, monkeypatch) -> None:
    """The observability JSONL sink must persist run events to disk.

    Without this, OTel / debugging tools can't replay a run.
    """
    monkeypatch.setenv("AGENTKIT_LOG_DIR", str(tmp_path))

    llm = create_llm_from_env()
    assert llm is not None

    try:
        await AgentOrchestrationService(
            llm_factory=lambda: llm,
        ).run(
            agent_name="general_chat",
            query="reply with one short sentence",
        )
    finally:
        await llm.close()

    run_files = list((tmp_path / "runs").glob("*/*.jsonl"))
    assert run_files, f"no JSONL log written under {tmp_path}/runs"

    records = [
        json.loads(line)
        for line in run_files[-1].read_text("utf-8").splitlines()
        if line.strip()
    ]
    event_types = [r["event_type"] for r in records]
    assert event_types[0] == "run_started"
    assert event_types[-1] == "run_completed"
    # Real runs always include at least one model output and one usage report
    assert "text_delta" in event_types
    assert "usage_report" in event_types
