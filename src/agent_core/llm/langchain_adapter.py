from typing import Any

from agent_core.llm.client import LLMResponse
from agent_core.memory.message import Message


class LangChainAdapterClient:
    def __init__(self, chat_model: Any) -> None:
        self.chat_model = chat_model

    async def chat(self, messages: list[Message], *, tools=None, stream: bool = False, **kwargs) -> LLMResponse:
        rendered = [message.to_openai() for message in messages]
        if hasattr(self.chat_model, "ainvoke"):
            response = await self.chat_model.ainvoke(rendered, **kwargs)
        else:
            response = self.chat_model.invoke(rendered, **kwargs)
        return LLMResponse(content=getattr(response, "content", str(response)))

    async def chat_stream(self, messages: list[Message], *, tools=None, **kwargs):
        response = await self.chat(messages, tools=tools, stream=False, **kwargs)
        yield response
