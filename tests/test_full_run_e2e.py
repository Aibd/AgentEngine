from __future__ import annotations

import json

from agent_core.llm.client import LLMResponse
from mock_llm import MockLLMClient
from services.agent_orchestration_service import AgentOrchestrationService


async def test_full_run_records_runtime_events_and_sse(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_CORE_LOG_DIR", str(tmp_path))
    service = AgentOrchestrationService()
    context, stream = service.create_streaming_context(
        request_id="req-e2e",
        query="hello",
        conversation_id="conv-e2e",
    )
    context.llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])

    result = await service.run(
        agent_name="general_chat",
        query="hello",
        context=context,
    )

    assert result == "ok"
    assert context.extras["run_id"].startswith("run_")
    assert context.extras["turn_id"].startswith("turn_")
    assert context.extras["runtime_events"][0].event_type == "run_started"
    assert context.extras["runtime_events"][-1].event_type == "run_completed"

    sse_events = []
    while not stream._queue.empty():
        event = await stream._queue.get()
        if event is not None and "comment" not in event:
            sse_events.append(event)
    # Updated for the streaming-protocol upgrade: turn boundaries (step / step_end)
    # and a usage report now bracket model output, matching what the React/CLI
    # renderers expect.
    assert [event["event"] for event in sse_events] == [
        "start",
        "step",
        "text",
        "step_end",
        "usage",
        "done",
    ]
    assert sse_events[2]["data"]["delta"] == "ok"

    log_path = tmp_path / "runs"
    files = list(log_path.glob("*/*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text("utf-8").splitlines()]
    assert [record["event_type"] for record in records] == [
        "run_started",
        "turn_started",
        "text_delta",
        "turn_ended",
        "usage_report",
        "run_completed",
    ]
