from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentengine.errors import ContextWindowExceededError
from agentengine.llm.client import LLMClient
from agentengine.memory.message import Message, Role


class Compactor(Protocol):
    """Compress a message history into a shorter, semantically equivalent one."""

    async def compact(self, messages: list[Message]) -> list[Message]:
        ...


@dataclass(slots=True)
class LLMSummaryCompactor:
    """Default compactor that asks an LLM to summarize older history."""

    llm: LLMClient
    keep_recent: int = 8
    max_input_chars: int = 60_000
    summary_prompt: str = (
        "Summarize the conversation history for a continuing coding agent. "
        "Preserve concrete user requirements, decisions, constraints, tool "
        "results, open questions, and any facts the next turn needs. Be terse."
    )

    async def compact(self, messages: list[Message]) -> list[Message]:
        system_messages = [
            msg
            for msg in messages
            if msg.role is Role.SYSTEM and not msg.metadata.get("compaction_summary")
        ]
        non_system = [msg for msg in messages if msg.role is not Role.SYSTEM]
        recent = _safe_recent_tail(non_system, keep_recent=self.keep_recent)
        history = non_system[: max(0, len(non_system) - len(recent))]

        previous_summaries = [
            msg.content
            for msg in messages
            if msg.role is Role.SYSTEM and msg.metadata.get("compaction_summary")
        ]
        if not history and not previous_summaries:
            return messages

        transcript = _render_transcript(history)
        if previous_summaries:
            transcript = (
                "Previous summaries:\n"
                + "\n\n".join(previous_summaries)
                + "\n\nOlder messages:\n"
                + transcript
            )
        transcript = transcript[-self.max_input_chars :]

        response = await self.llm.chat(
            [
                Message.system(self.summary_prompt),
                Message.user(transcript or "No prior messages."),
            ],
            stream=False,
        )
        summary = (response.content or "").strip()
        if not summary:
            raise ContextWindowExceededError("compaction produced an empty summary")

        summary_message = Message(
            Role.SYSTEM,
            "Conversation summary so far:\n" + summary,
            metadata={"compaction_summary": True},
        )
        return system_messages + [summary_message] + recent


def _render_transcript(messages: list[Message]) -> str:
    lines: list[str] = []
    for msg in messages:
        content = msg.content or ""
        if msg.tool_calls:
            tool_names = [
                (call.get("function") or {}).get("name", "")
                for call in msg.tool_calls
            ]
            content = (content + "\n" if content else "") + (
                "tool calls: " + ", ".join(name for name in tool_names if name)
            )
        if msg.tool_call_id:
            content = f"tool_call_id={msg.tool_call_id}\n{content}"
        lines.append(f"{msg.role.value}: {content}".strip())
    return "\n\n".join(lines)


def _safe_recent_tail(messages: list[Message], *, keep_recent: int) -> list[Message]:
    if keep_recent <= 0 or len(messages) <= keep_recent:
        return list(messages)
    start = max(0, len(messages) - keep_recent)
    while start > 0 and messages[start].role is Role.TOOL:
        start -= 1
    return messages[start:]


__all__ = ["Compactor", "LLMSummaryCompactor"]
