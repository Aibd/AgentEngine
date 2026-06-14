"""Tests for protecting activated skills during trim and compaction."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agentengine.memory.memory import Memory
from agentengine.memory.message import Message, Role
from agentengine.runtime.compaction import LLMSummaryCompactor


def _skill_activation_pair(skill_name: str, call_id: str = "call_1") -> tuple[Message, Message]:
    assistant = Message.assistant(
        "",
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "Skill", "arguments": f'{{"skill": "{skill_name}"}}'},
            }
        ],
    )
    result = Message.tool(
        f"<skill_content name=\"{skill_name}\">instructions</skill_content>",
        tool_call_id=call_id,
        metadata={"skill_activation": True, "skill_name": skill_name},
    )
    return assistant, result


class TestMemoryTrim:
    def test_keeps_skill_activation_pair_when_trimming(self) -> None:
        memory = Memory(max_messages=4)
        memory.add_system_message("sys")
        for i in range(5):
            memory.add_user_message(f"msg{i}")
        assistant, result = _skill_activation_pair("data-analysis")
        memory.add_assistant_message(
            assistant.content,
            tool_calls=assistant.tool_calls,
        )
        memory.add_tool_message(
            result.content,
            tool_call_id=result.tool_call_id,
            metadata=result.metadata,
        )

        # max_messages=4 means system + up to 2 non-system messages after trimming.
        # The protected pair should survive even though older messages are dropped.
        assert len(memory.messages) >= 3
        assert any(
            m.role == Role.ASSISTANT and m.tool_calls for m in memory.messages
        )
        assert any(
            m.role == Role.TOOL and m.metadata.get("skill_activation")
            for m in memory.messages
        )

    def test_drops_unprotected_messages_before_skill_pair(self) -> None:
        memory = Memory(max_messages=3)
        memory.add_system_message("sys")
        memory.add_user_message("old")
        assistant, result = _skill_activation_pair("data-analysis")
        memory.add_assistant_message("", tool_calls=assistant.tool_calls)
        memory.add_tool_message(
            result.content,
            tool_call_id=result.tool_call_id,
            metadata=result.metadata,
        )

        assert len(memory.messages) == 3
        assert memory.messages[0].role == Role.SYSTEM
        assert memory.messages[1].role == Role.ASSISTANT
        assert memory.messages[2].role == Role.TOOL
        assert memory.messages[2].metadata.get("skill_activation") is True

    def test_keeps_latest_activation_for_duplicate_skill(self) -> None:
        memory = Memory(max_messages=6)
        memory.add_system_message("sys")
        assistant1, result1 = _skill_activation_pair("demo", "call_1")
        memory.add_assistant_message("", tool_calls=assistant1.tool_calls)
        memory.add_tool_message(
            result1.content,
            tool_call_id=result1.tool_call_id,
            metadata=result1.metadata,
        )
        memory.add_user_message("next")
        assistant2, result2 = _skill_activation_pair("demo", "call_2")
        memory.add_assistant_message("", tool_calls=assistant2.tool_calls)
        memory.add_tool_message(
            result2.content,
            tool_call_id=result2.tool_call_id,
            metadata=result2.metadata,
        )

        # With max_messages=6, system + 4 protected messages fit exactly, so both
        # pairs would be kept. Trim to a tighter budget to force deduplication.
        memory.max_messages = 4
        memory.add_user_message("trigger trim")

        tool_messages = [m for m in memory.messages if m.role == Role.TOOL]
        assert len(tool_messages) == 1
        assert tool_messages[0].tool_call_id == "call_2"


class TestCompaction:
    @pytest.mark.asyncio
    async def test_protects_skill_pair_from_summarization(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = AsyncMock(content="summary")
        compactor = LLMSummaryCompactor(llm=llm, keep_recent=2)

        messages = [
            Message.system("sys"),
            Message.user("old question 1"),
            Message.user("old question 2"),
        ]
        assistant, result = _skill_activation_pair("data-analysis")
        messages.append(assistant)
        messages.append(result)
        messages.append(Message.user("recent question"))

        compacted = await compactor.compact(messages)

        # The skill activation pair must survive unchanged.
        tool_msgs = [m for m in compacted if m.role == Role.TOOL]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].metadata.get("skill_activation") is True
        assert "<skill_content name=\"data-analysis\">" in tool_msgs[0].content

        # The assistant tool-call message must also survive.
        assistant_msgs = [m for m in compacted if m.role == Role.ASSISTANT]
        assert any(m.tool_calls for m in assistant_msgs)

    @pytest.mark.asyncio
    async def test_no_llm_call_when_only_protected_messages_exist(self) -> None:
        llm = AsyncMock()
        compactor = LLMSummaryCompactor(llm=llm, keep_recent=2)

        messages = [Message.system("sys")]
        assistant, result = _skill_activation_pair("demo")
        messages.append(assistant)
        messages.append(result)

        compacted = await compactor.compact(messages)

        llm.chat.assert_not_awaited()
        assert len(compacted) == 3
