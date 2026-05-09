"""Deterministic mock LLM client for tests.

Returns canned LLMResponses from a queue, records every call's kwargs,
and simulates streaming by yielding chunks (including tool_call deltas)
that match what a real OpenAI-compatible backend produces.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from agentengine.llm.client import LLMChunk, LLMResponse
from agentengine.memory.message import Message


class MockLLMClient:
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses: list[LLMResponse] = list(responses) if responses else []
        self._idx: int = 0
        self.calls: list[dict[str, Any]] = []

    def enqueue(self, response: LLMResponse) -> None:
        self._responses.append(response)

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": [m.to_openai() if isinstance(m, Message) else m for m in messages],
                "tools": tools,
                "stream": stream,
                **kwargs,
            }
        )
        if stream:
            return await self._collect_from_stream(messages, tools=tools, **kwargs)
        return self._next_response()

    async def chat_stream(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        self.calls.append(
            {
                "messages": [m.to_openai() if isinstance(m, Message) else m for m in messages],
                "tools": tools,
                "stream": True,
                **kwargs,
            }
        )
        response = self._next_response()
        if response.content:
            yield LLMChunk(content=response.content)
        if response.reasoning_content:
            yield LLMChunk(reasoning_content=response.reasoning_content)
        if response.tool_calls:
            chunk = LLMChunk(finish_reason="tool_calls")
            chunk.raw = {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": i,
                                    "id": tc.get("id", f"call_{i}"),
                                    "type": "function",
                                    "function": {
                                        "name": tc["function"]["name"],
                                        "arguments": tc["function"]["arguments"],
                                    },
                                }
                                for i, tc in enumerate(response.tool_calls)
                            ]
                        }
                    }
                ]
            }
            yield chunk
        yield LLMChunk(usage=response.usage, finish_reason=response.finish_reason)

    async def _collect_from_stream(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMResponse:
        collected = LLMResponse()
        async for chunk in self.chat_stream(messages, **kwargs):
            if chunk.content:
                collected.content += chunk.content
            if chunk.reasoning_content:
                collected.reasoning_content += chunk.reasoning_content
            if chunk.finish_reason:
                collected.finish_reason = chunk.finish_reason
            if chunk.usage:
                collected.usage = chunk.usage
            if chunk.raw is not None:
                for choice in chunk.raw.get("choices", []) or []:
                    delta = choice.get("delta", {}) or {}
                    for tc in delta.get("tool_calls", []) or []:
                        collected.tool_calls.append(
                            {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": tc.get("function", {}),
                            }
                        )
        return collected

    def _next_response(self) -> LLMResponse:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
            self._idx += 1
            return resp
        return LLMResponse(content="Mock response (fallback)")
