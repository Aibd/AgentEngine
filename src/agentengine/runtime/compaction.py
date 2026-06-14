from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentengine.errors import ContextWindowExceededError
from agentengine.llm.interfaces import LLMClient
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

        # Protected skill activation pairs (assistant tool call + tool result)
        # are kept verbatim and never summarized.
        protected_pairs = self._extract_protected_pairs(non_system)
        protected_ids = {id(msg) for pair in protected_pairs for msg in pair}
        summarizable = [msg for msg in non_system if id(msg) not in protected_ids]

        recent = _safe_recent_tail(summarizable, keep_recent=self.keep_recent)
        history = summarizable[: max(0, len(summarizable) - len(recent))]

        previous_summaries = [
            msg.content
            for msg in messages
            if msg.role is Role.SYSTEM and msg.metadata.get("compaction_summary")
        ]
        if not history and not previous_summaries:
            return self._reassemble(system_messages, [], protected_pairs, non_system)

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
        return self._reassemble(
            system_messages, [summary_message], protected_pairs, recent
        )

    @staticmethod
    def _extract_protected_pairs(
        messages: list[Message],
    ) -> list[tuple[Message, Message]]:
        """Find assistant/tool-result pairs where the result is a skill activation."""
        pairs: list[tuple[Message, Message]] = []
        for idx, msg in enumerate(messages):
            if msg.role is not Role.TOOL or not msg.metadata.get("skill_activation"):
                continue
            tool_call_id = msg.tool_call_id
            if not tool_call_id:
                continue
            assistant_msg: Message | None = None
            for prev in reversed(messages[:idx]):
                if prev.role is not Role.ASSISTANT or not prev.tool_calls:
                    continue
                if any(call.get("id") == tool_call_id for call in prev.tool_calls):
                    assistant_msg = prev
                    break
            if assistant_msg is not None:
                pairs.append((assistant_msg, msg))
        return pairs

    @staticmethod
    def _reassemble(
        system_messages: list[Message],
        summary_messages: list[Message],
        protected_pairs: list[tuple[Message, Message]],
        tail_messages: list[Message],
    ) -> list[Message]:
        """Put the final message list together preserving tool-call pairings."""
        protected_ids = {id(msg) for pair in protected_pairs for msg in pair}
        tail_without_protected = [m for m in tail_messages if id(m) not in protected_ids]

        result = list(system_messages)
        result.extend(summary_messages)
        result.extend(msg for pair in protected_pairs for msg in pair)
        result.extend(tail_without_protected)
        return result


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
