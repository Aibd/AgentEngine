from __future__ import annotations

import httpx
import pytest

import agentengine.llm.openai_compat as openai_compat
from agentengine.errors import LLMHTTPError, LLMRateLimitError, LLMStreamError
from agentengine.llm.openai_compat import OpenAICompatibleClient
from agentengine.memory.message import Message


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


class _InterruptedSSEStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        raise httpx.RemoteProtocolError("stream interrupted")


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
        with pytest.raises(LLMHTTPError, match="forbidden reason") as exc_info:
            await client.chat([Message.user("hello")])
    finally:
        await client.close()

    assert exc_info.value.error_code == "llm_http_error"
    assert exc_info.value.error_status_code == 403
    assert exc_info.value.details["body"] == "forbidden reason"


async def test_post_does_not_retry_non_retryable_4xx(monkeypatch):
    monkeypatch.setattr(openai_compat.asyncio, "sleep", _no_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, text="forbidden reason", request=request)

    client = _client_with_transport(handler, max_retries=2)
    try:
        with pytest.raises(LLMHTTPError):
            await client.chat([Message.user("hello")])
    finally:
        await client.close()

    assert calls == 1


async def test_post_retries_429_using_retry_after(monkeypatch):
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(openai_compat.asyncio, "sleep", record_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                text="slow down",
                headers={"Retry-After": "2.5"},
                request=request,
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            request=request,
        )

    client = _client_with_transport(handler, max_retries=1)
    try:
        result = await client.chat([Message.user("hello")])
    finally:
        await client.close()

    assert result.content == "ok"
    assert calls == 2
    assert sleeps == [2.5]


async def test_post_raises_structured_rate_limit_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text="too many requests",
            headers={"Retry-After": "7"},
            request=request,
        )

    client = _client_with_transport(handler, max_retries=0)
    try:
        with pytest.raises(LLMRateLimitError) as exc_info:
            await client.chat([Message.user("hello")])
    finally:
        await client.close()

    assert exc_info.value.error_code == "llm_rate_limited"
    assert exc_info.value.is_retryable is True
    assert exc_info.value.details["retry_after_seconds"] == 7.0


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


async def test_stream_does_not_retry_after_partial_chunk(monkeypatch):
    monkeypatch.setattr(openai_compat.asyncio, "sleep", _no_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            stream=_InterruptedSSEStream(),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = _client_with_transport(handler, max_retries=2)
    chunks = []
    try:
        with pytest.raises(LLMStreamError) as exc_info:
            async for chunk in client.chat_stream([Message.user("hello")]):
                chunks.append(chunk)
    finally:
        await client.close()

    assert [chunk.content for chunk in chunks] == ["hi"]
    assert calls == 1
    assert exc_info.value.is_retryable is False
    assert exc_info.value.details["cause_code"] == "llm_connection_error"


async def test_stream_provider_error_is_structured():
    body = 'data: {"error":{"message":"bad stream","type":"provider_error"}}\n\n'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = _client_with_transport(handler, max_retries=1)
    try:
        with pytest.raises(LLMStreamError) as exc_info:
            _ = [chunk async for chunk in client.chat_stream([Message.user("hello")])]
    finally:
        await client.close()

    assert exc_info.value.error_code == "llm_stream_error"
    assert exc_info.value.is_retryable is False
    assert exc_info.value.details["provider_error"]["type"] == "provider_error"
