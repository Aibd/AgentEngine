from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentengine import (  # noqa: E402
    AgentContext,
    AgentEngine,
    AgentPreset,
    PersistencePort,
    Tool,
    ToolCollection,
)
from agentengine.llm.client import LLMChunk, LLMResponse  # noqa: E402


class InMemoryPersistence:
    def __init__(self) -> None:
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.runs: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []

    async def save_run(self, **kwargs: Any) -> None:
        self.runs.append(dict(kwargs))

    async def save_messages(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        self.messages[conversation_id] = list(messages)

    async def load_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return list(self.messages.get(conversation_id, []))

    async def save_artifact(self, run_id: str, artifact_type: str, data: dict[str, Any]) -> None:
        self.artifacts.append(
            {"run_id": run_id, "artifact_type": artifact_type, "data": data}
        )


class OrderLookupTool(Tool):
    name = "lookup_order"
    description = "Look up an order by id."
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }

    async def run(self, **kwargs: Any) -> str:
        return f"order {kwargs['order_id']} is packed"


class ToolCallingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, *, tools=None, stream=False, **kwargs):
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_stream(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield LLMChunk(
                finish_reason="tool_calls",
                raw={
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "lookup_order",
                                            "arguments": '{"order_id": "A-100"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            )
            return
        yield LLMChunk(
            content="Order A-100 is packed.",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


async def main() -> None:
    persistence: PersistencePort = InMemoryPersistence()
    engine = AgentEngine(
        presets={"orders": AgentPreset(name="orders", instructions="Use tools when needed.")},
        persistence=persistence,
    )
    context = AgentContext(
        request_id="example-tools",
        query="Where is order A-100?",
        llm=ToolCallingLLM(),
        conversation_id="conv-orders",
        tool_collection=ToolCollection([OrderLookupTool()]),
    )

    async def print_event(event) -> None:
        print(event.to_dict()["event_type"])

    result = await engine.run(
        agent_name="orders",
        query=context.query,
        context=context,
        on_event=print_event,
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
