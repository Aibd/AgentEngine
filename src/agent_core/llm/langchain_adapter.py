from typing import Any, AsyncIterator

from agent_core.llm.client import LLMChunk, LLMResponse
from agent_core.memory.message import Message


class LangChainAdapterClient:
    """Thin adapter so any LangChain ChatModel can stand in as an LLMClient."""

    def __init__(self, chat_model: Any) -> None:
        self.chat_model = chat_model

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        rendered = [message.to_openai() for message in messages]
        if hasattr(self.chat_model, "ainvoke"):
            response = await self.chat_model.ainvoke(rendered, **kwargs)
        else:
            response = self.chat_model.invoke(rendered, **kwargs)
        return LLMResponse(content=getattr(response, "content", str(response)))

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        response = await self.chat(messages, tools=tools, stream=False, **kwargs)
        yield LLMChunk(content=response.content)
