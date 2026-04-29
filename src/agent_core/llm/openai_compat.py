"""OpenAI-compatible chat client.

Production-grade backend for OpenAI / DeepSeek / Qwen / vLLM / LiteLLM proxies.
Reuses one httpx.AsyncClient, retries with exponential backoff, accumulates
streaming tool_calls deltas, and surfaces reasoning_content for thinking models.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from agent_core.errors import (
    LLMConnectionError,
    LLMError,
    LLMHTTPError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agent_core.llm.client import LLMChunk, LLMResponse
from agent_core.memory.message import Message


logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        chat_path: str = "/v1/chat/completions",
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.chat_path = chat_path
        self.timeout = timeout
        self.max_retries = max_retries

        self._client: httpx.AsyncClient | None = None

    # -- LLMClient ------------------------------------------------------

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if stream:
            return await self._collect_stream(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        return await self._post(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        async for chunk in self._stream(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield chunk

    # -- Lifecycle ------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- Internal -------------------------------------------------------

    def _build_payload(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": [m.to_openai() for m in messages],
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)
        return payload

    async def _post(
        self,
        messages: list[Message],
        **kwargs: Any,
    ) -> LLMResponse:
        payload = self._build_payload(messages, stream=False, **kwargs)

        last_exc: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            attempt_no = attempt + 1
            started_at = time.perf_counter()
            logger.debug(
                "llm_post_start attempt=%d model=%s messages=%d tools=%d",
                attempt_no,
                payload.get("model"),
                len(payload.get("messages", [])),
                len(payload.get("tools", []) or []),
            )
            try:
                resp = await self.client.post(self.chat_path, json=payload)
                self._raise_for_status(resp)
                logger.debug(
                    "llm_post_finish attempt=%d model=%s status=%d elapsed=%.3fs",
                    attempt_no,
                    payload.get("model"),
                    resp.status_code,
                    time.perf_counter() - started_at,
                )
                return self._parse_response(resp.json())
            except (LLMError, httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = self._to_llm_error(exc)
                status_code = last_exc.error_status_code
                if attempt < self.max_retries and last_exc.is_retryable:
                    logger.warning(
                        "llm_post_retry attempt=%d model=%s status=%s error=%s",
                        attempt_no,
                        payload.get("model"),
                        status_code,
                        last_exc.error_code,
                    )
                    await asyncio.sleep(self._retry_delay(attempt, last_exc))
                else:
                    logger.error(
                        "llm_post_failed attempt=%d model=%s status=%s error=%s elapsed=%.3fs",
                        attempt_no,
                        payload.get("model"),
                        status_code,
                        last_exc.error_code,
                        time.perf_counter() - started_at,
                    )
                    raise last_exc
        assert last_exc is not None
        raise last_exc

    async def _collect_stream(
        self,
        messages: list[Message],
        **kwargs: Any,
    ) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_map: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}

        async for chunk in self._stream(messages, **kwargs):
            if chunk.content:
                content_parts.append(chunk.content)
            if chunk.reasoning_content:
                reasoning_parts.append(chunk.reasoning_content)
            if chunk.usage:
                usage = chunk.usage
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason

            if chunk.raw is None:
                continue
            for choice in chunk.raw.get("choices", []) or []:
                delta = choice.get("delta", {}) or {}
                for tc in delta.get("tool_calls", []) or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls_map.setdefault(
                        idx,
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    func = tc.get("function", {}) or {}
                    if func.get("name"):
                        slot["function"]["name"] = func["name"]
                    if func.get("arguments"):
                        slot["function"]["arguments"] += func["arguments"]

        return LLMResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=list(tool_calls_map.values()) if tool_calls_map else [],
            finish_reason=finish_reason or "stop",
            usage=usage,
        )

    async def _stream(
        self,
        messages: list[Message],
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        payload = self._build_payload(messages, stream=True, **kwargs)

        last_exc: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            attempt_no = attempt + 1
            started_at = time.perf_counter()
            logger.debug(
                "llm_stream_http_start attempt=%d model=%s messages=%d tools=%d",
                attempt_no,
                payload.get("model"),
                len(payload.get("messages", [])),
                len(payload.get("tools", []) or []),
            )
            try:
                async with self.client.stream("POST", self.chat_path, json=payload) as resp:
                    self._raise_for_status(resp)
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        chunk = self._parse_chunk(data)
                        if (
                            chunk.content
                            or chunk.reasoning_content
                            or chunk.usage
                            or chunk.finish_reason
                            or (
                                data.get("choices")
                                and any(
                                    (c.get("delta") or {}).get("tool_calls")
                                    for c in data.get("choices", [])
                                )
                            )
                        ):
                            chunk.raw = data
                            yield chunk
                logger.debug(
                    "llm_stream_http_finish attempt=%d model=%s elapsed=%.3fs",
                    attempt_no,
                    payload.get("model"),
                    time.perf_counter() - started_at,
                )
                return
            except (LLMError, httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = self._to_llm_error(exc)
                status_code = last_exc.error_status_code
                if attempt < self.max_retries and last_exc.is_retryable:
                    logger.warning(
                        "llm_stream_http_retry attempt=%d model=%s status=%s error=%s",
                        attempt_no,
                        payload.get("model"),
                        status_code,
                        last_exc.error_code,
                    )
                    await asyncio.sleep(self._retry_delay(attempt, last_exc))
                else:
                    logger.error(
                        "llm_stream_http_failed attempt=%d model=%s status=%s error=%s elapsed=%.3fs",
                        attempt_no,
                        payload.get("model"),
                        status_code,
                        last_exc.error_code,
                        time.perf_counter() - started_at,
                    )
                    raise last_exc
        assert last_exc is not None
        raise last_exc

    def _raise_for_status(self, resp: httpx.Response) -> None:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = resp.text[:2000]
            message = f"{exc}; response body: {body}"
            retryable = self._is_retryable_status(resp.status_code)
            details = {"url": str(exc.request.url)}
            if resp.status_code == 429:
                raise LLMRateLimitError(
                    message,
                    status_code=resp.status_code,
                    body=body,
                    retry_after_seconds=self._retry_after_seconds(resp),
                    details=details,
                ) from exc
            raise LLMHTTPError(
                message,
                status_code=resp.status_code,
                body=body,
                retryable=retryable,
                details=details,
            ) from exc

    def _to_llm_error(self, exc: BaseException) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(
                str(exc) or "LLM request timed out",
                details=self._request_details(exc),
            )
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            return LLMHTTPError(
                f"{exc}; response body: {response.text[:2000]}",
                status_code=response.status_code,
                body=response.text[:2000],
                retryable=self._is_retryable_status(response.status_code),
                details=self._request_details(exc),
            )
        if isinstance(exc, httpx.TransportError):
            return LLMConnectionError(
                str(exc) or "LLM transport error",
                details=self._request_details(exc),
            )
        return LLMError(str(exc) or exc.__class__.__name__)

    def _request_details(self, exc: httpx.HTTPError) -> dict[str, Any]:
        request = getattr(exc, "request", None)
        if request is None:
            return {}
        return {"url": str(request.url)}

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code in _RETRYABLE_STATUS_CODES

    def _retry_delay(self, attempt: int, error: LLMError) -> float:
        retry_after = error.details.get("retry_after_seconds")
        if isinstance(retry_after, int | float):
            return max(0.0, float(retry_after))
        return float(2 ** attempt)

    def _retry_after_seconds(self, resp: httpx.Response) -> float | None:
        raw = resp.headers.get("Retry-After")
        if raw is None or raw.strip() == "":
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())

    # -- Parsing --------------------------------------------------------

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        return LLMResponse(
            content=message.get("content") or "",
            reasoning_content=message.get("reasoning_content") or "",
            tool_calls=message.get("tool_calls") or [],
            finish_reason=choice.get("finish_reason") or "stop",
            usage=data.get("usage") or {},
            raw=data,
        )

    def _parse_chunk(self, data: dict[str, Any]) -> LLMChunk:
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta", {}) or {}
        return LLMChunk(
            content=delta.get("content") or "",
            reasoning_content=delta.get("reasoning_content") or "",
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage") or {},
        )
