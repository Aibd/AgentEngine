from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentengine import AgentContext, AgentEngine, AgentPreset  # noqa: E402
from agentengine.llm.client import LLMChunk, LLMResponse  # noqa: E402


class EchoLLM:
    async def chat(self, messages, *, tools=None, stream=False, **kwargs):
        return LLMResponse(content="Hello from an injected LLM.", finish_reason="stop")

    async def chat_stream(self, messages, *, tools=None, **kwargs):
        yield LLMChunk(content="Hello from an injected LLM.", finish_reason="stop")


async def main() -> None:
    engine = AgentEngine(
        presets={
            "chat": AgentPreset(
                name="chat",
                instructions="Answer briefly.",
            )
        }
    )
    context = AgentContext(
        request_id="example-minimal",
        query="hello",
        llm=EchoLLM(),
    )
    result = await engine.run(agent_name="chat", query=context.query, context=context)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
