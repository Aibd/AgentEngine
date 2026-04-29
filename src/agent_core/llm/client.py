from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from agent_core.memory.message import Message


@dataclass(slots=True)
class LLMResponse:
    """Non-streaming chat response.

    `tool_calls` stores OpenAI raw dict format so it can be written straight
    back into Memory and resent on the next turn without conversion.
    """

    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class LLMChunk:
    """A single streaming delta."""

    content: str = ""
    reasoning_content: str = ""
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] | None = None


class LLMClient(Protocol):
    """Any backend that speaks the chat-completions shape.

    Implementations: OpenAICompatibleClient (httpx), MockLLMClient (tests/mock_llm.py).
    """

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        ...

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        ...
