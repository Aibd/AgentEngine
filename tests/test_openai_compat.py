from __future__ import annotations

import httpx
import pytest

import agent_core.llm.openai_compat as openai_compat
from agent_core.llm.openai_compat import OpenAICompatibleClient
from agent_core.memory.message import Message


async def _no_sleep(delay: float) -> None:
    return None


def _client_with_transport(
    handler,
    *,
    max_retries: int = 1,
) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(
        base_url="https://example.test",
        api_key="sk-test",
        model="test-model",
        max_retries=max_retries,
    )
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )
    return client


async def test_post_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(openai_compat.asyncio, "sleep", _no_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text="temporary failure", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"total_tokens": 3},
            },
            request=request,
        )

    client = _client_with_transport(handler, max_retries=1)
    try:
        result = await client.chat([Message.user("hello")])
    finally:
        await client.close()

    assert result.content == "ok"
    assert result.usage == {"total_tokens": 3}
    assert calls == 2


async def test_post_raises_status_error_with_response_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden reason", request=request)

    client = _client_with_transport(handler, max_retries=0)
    try:
        with pytest.raises(httpx.HTTPStatusError, match="forbidden reason"):
            await client.chat([Message.user("hello")])
    finally:
        await client.close()


async def test_stream_retries_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(openai_compat.asyncio, "sleep", _no_sleep)
    calls = 0
    body = (
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.TimeoutException("timed out", request=request)
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = _client_with_transport(handler, max_retries=1)
    try:
        chunks = [chunk async for chunk in client.chat_stream([Message.user("hello")])]
    finally:
        await client.close()

    assert [chunk.content for chunk in chunks if chunk.content] == ["hi"]
    assert chunks[-1].finish_reason == "stop"
    assert calls == 2
