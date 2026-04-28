from collections.abc import AsyncIterator
from typing import Any
import json

import httpx

from agent_core.llm.client import LLMChunk, LLMResponse, ToolCall
from agent_core.memory.message import Message


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        chat_path: str = "/v1/chat/completions",
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.chat_path = chat_path
        self.timeout = timeout

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        if stream:
            content = []
            async for chunk in self.chat_stream(messages, tools=tools, **kwargs):
                content.append(chunk.content)
            return LLMResponse(content="".join(content))

        payload = self._payload(messages, tools=tools, stream=False, **kwargs)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self._url(), json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            args = function.get("arguments") or "{}"
            try:
                parsed_args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                parsed_args = {"raw": args}
            tool_calls.append(ToolCall(id=call.get("id", ""), name=function.get("name", ""), arguments=parsed_args))
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            raw=data,
        )

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        payload = self._payload(messages, tools=tools, stream=True, **kwargs)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self._url(), json=payload, headers=self._headers()) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:].strip()
                    if line == "[DONE]":
                        break
                    data = json.loads(line)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    yield LLMChunk(content=delta.get("content") or "", raw=data)

    def _payload(self, messages: list[Message], *, tools: list[dict[str, Any]] | None, stream: bool, **kwargs: Any) -> dict[str, Any]:
        payload = {
            "model": kwargs.pop("model", self.model),
            "messages": [message.to_openai() for message in messages],
            "stream": stream,
            **kwargs,
        }
        if tools:
            payload["tools"] = tools
            payload.setdefault("tool_choice", "auto")
        return payload

    def _url(self) -> str:
        return f"{self.base_url}{self.chat_path}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
