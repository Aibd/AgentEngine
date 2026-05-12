from __future__ import annotations

import pytest

from agentengine.llm.interfaces import LLMResponse
from agentengine.llm.env import LLMConfigError, create_llm_from_env
from mock_llm import MockLLMClient
from agentengine.memory.message import Message


class TestMockLLMClient:
    async def test_returns_enqueued_responses(self):
        client = MockLLMClient([
            LLMResponse(content="first"),
            LLMResponse(content="second"),
        ])
        r1 = await client.chat([Message.user("q1")])
        r2 = await client.chat([Message.user("q2")])
        assert r1.content == "first"
        assert r2.content == "second"

    async def test_fallback_when_queue_empty(self):
        client = MockLLMClient()
        r = await client.chat([Message.user("hi")])
        assert "fallback" in r.content.lower()

    async def test_records_calls(self):
        client = MockLLMClient([LLMResponse(content="ok")])
        await client.chat(
            [Message.user("test")],
            tools=[{"type": "function", "function": {"name": "t1"}}],
            temperature=0.5,
        )
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["messages"][0]["content"] == "test"
        assert call["tools"][0]["function"]["name"] == "t1"
        assert call["temperature"] == 0.5

    async def test_chat_stream_content(self):
        client = MockLLMClient([
            LLMResponse(content="streaming result", finish_reason="stop"),
        ])
        chunks = []
        async for chunk in client.chat_stream([Message.user("q")]):
            chunks.append(chunk)
        contents = [c.content for c in chunks if c.content]
        assert "streaming result" in contents

    async def test_chat_stream_with_tool_calls(self):
        client = MockLLMClient([
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                    }
                ],
            ),
        ])
        chunks = []
        async for chunk in client.chat_stream([Message.user("q")]):
            chunks.append(chunk)
        tool_chunks = [c for c in chunks if c.raw is not None]
        assert tool_chunks, "expected at least one chunk carrying raw tool_calls"

    async def test_chat_stream_collected_via_chat(self):
        """stream=True via chat() should collect chunks into a complete response."""
        client = MockLLMClient([
            LLMResponse(
                content="hello",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{}'},
                    }
                ],
                finish_reason="tool_calls",
            ),
        ])
        result = await client.chat([Message.user("q")], stream=True)
        assert result.content == "hello"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "echo"


class TestLLMFactory:
    def test_create_llm_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")

        client = create_llm_from_env()

        assert client is not None
        assert client.base_url == "https://api.deepseek.com"
        assert client.model == "deepseek-v4-pro"
        assert client.chat_path == "/v1/chat/completions"

    def test_create_llm_from_env_optional_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)

        assert create_llm_from_env(required=False) is None

    def test_create_llm_from_env_required_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)

        with pytest.raises(LLMConfigError):
            create_llm_from_env()
