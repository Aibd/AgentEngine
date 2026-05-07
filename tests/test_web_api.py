"""Integration tests for the FastAPI SSE endpoint.

The web frontend depends on these wire shapes — keep them stable.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from agent_core.llm.client import LLMResponse
from mock_llm import MockLLMClient
from services import web_api


pytestmark = pytest.mark.asyncio


async def _consume_sse(response: httpx.Response) -> AsyncIterator[dict]:
    """Yield v2 SSE frames parsed from a streaming response."""
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            lines = raw.splitlines()
            event_name = "message"
            data_lines = []
            for line in lines:
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            yield {"event": event_name, "data": json.loads(payload)}


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    """Force the orchestration service to use a mock LLM regardless of env."""

    def _factory_returns_mock():
        return MockLLMClient([
            LLMResponse(
                content="response from mock",
                finish_reason="stop",
                usage={"prompt_tokens": 7, "completion_tokens": 3},
            ),
        ])

    monkeypatch.setattr(web_api, "_default_llm_factory_for_tests", _factory_returns_mock, raising=False)
    # The web_api module reuses AgentOrchestrationService's default factory;
    # patch that directly so context.llm gets injected at request time.
    from services import agent_orchestration_service as orch_module
    monkeypatch.setattr(orch_module, "_default_llm_factory", _factory_returns_mock)


async def test_health_endpoint() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "model" in body


async def test_run_stream_emits_full_event_sequence() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        async with client.stream(
            "GET",
            "/api/runs/stream",
            params={
                "query": "hello",
                "agent_name": "general_chat",
                "conversation_id": "test-conv",
            },
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-streaming-protocol"] == "agent-core.sse.v2"
            assert response.headers["x-conversation-id"] == "test-conv"
            events: list[dict] = []
            async for evt in _consume_sse(response):
                events.append(evt)

    types = [e["event"] for e in events]
    assert types[0] == "start"
    assert "step" in types
    assert "step_end" in types
    assert "usage" in types
    assert types[-1] == "done"

    # Schema invariants the frontend relies on
    for evt in events:
        assert "event" in evt
        assert "data" in evt
        assert evt["data"]["request_id"].startswith("web-")
        assert evt["data"]["conversation_id"] == "test-conv"
        assert "responseType" not in evt["data"]


async def test_run_stream_rejects_empty_query() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/runs/stream",
            params={"query": "   ", "agent_name": "general_chat"},
        )
    # FastAPI's Query(min_length=1) returns 422 for whitespace-stripped empty.
    # Our explicit handler returns 400 if it slips through. Either is fine —
    # the frontend just needs a non-200.
    assert response.status_code in (400, 422)
