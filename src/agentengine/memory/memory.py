from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any, Iterable

from agentengine.memory.message import Message, Role

if TYPE_CHECKING:
    from agentengine.persistence.port import PersistencePort

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~2 chars per token for mixed CJK/ASCII text."""
    return max(1, len(text) // 2)


@dataclass
class Memory:
    """Ordered message store with optional bounded size.

    Trimming is controlled by two limits (either can be 0 to disable):
    - ``max_messages``: drop oldest non-system messages when count exceeds this.
    - ``max_tokens``: drop oldest non-system messages when estimated token
      count exceeds this.

    When both are set, the tighter limit wins.  System messages are always
    preserved.
    """

    messages: list[Message] = field(default_factory=list)
    max_messages: int = 0
    max_tokens: int = 0
    _lock: Any = field(default_factory=RLock, init=False, repr=False)

    def append(self, message: Message) -> None:
        with self._lock:
            self.messages.append(message)
            self._trim()

    def extend(self, messages: Iterable[Message]) -> None:
        with self._lock:
            for message in messages:
                self.messages.append(message)
            self._trim()

    def extend_atomic(self, messages: Iterable[Message]) -> None:
        """Append multiple messages and trim once, treating the batch as a unit.

        This prevents a tight ``max_messages`` budget from dropping part of a
        logically indivisible message group (e.g. an assistant tool-call and its
        corresponding tool result).
        """
        with self._lock:
            self.messages.extend(messages)
            self._trim()

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()

    def replace(self, messages: Iterable[Message]) -> None:
        with self._lock:
            self.messages = list(messages)
            self._trim()

    def snapshot(self) -> list[Message]:
        """Return a stable copy of the current messages.

        Direct `messages` access remains available for compatibility, but
        concurrent readers should prefer this method.
        """
        with self._lock:
            return list(self.messages)

    def to_openai(self) -> list[dict[str, Any]]:
        return [message.to_openai() for message in self.snapshot()]

    # Convenience helpers ------------------------------------------------

    def add_system_message(self, content: str) -> None:
        self.append(Message.system(content))

    def add_user_message(self, content: str, *, base64_image: str | None = None) -> None:
        self.append(Message.user(content, base64_image=base64_image))

    def add_assistant_message(
        self,
        content: str = "",
        *,
        reasoning_content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.append(
            Message.assistant(
                content,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
            )
        )

    def add_tool_message(
        self, content: str, *, tool_call_id: str, metadata: dict[str, Any] | None = None
    ) -> None:
        msg = Message.tool(content, tool_call_id=tool_call_id)
        if metadata:
            msg.metadata.update(metadata)
        self.append(msg)

    def last_user_message(self) -> str:
        with self._lock:
            for message in reversed(self.messages):
                if message.role == Role.USER:
                    return message.content
        return ""

    def last_assistant_message(self) -> str:
        with self._lock:
            for message in reversed(self.messages):
                if message.role == Role.ASSISTANT and message.content:
                    return message.content
        return ""

    # Persistence --------------------------------------------------------

    async def load_from_db(
        self,
        persistence: PersistencePort,
        conversation_id: str,
    ) -> None:
        """Load prior messages from the persistence layer into memory.

        Existing messages are **not** cleared — loaded messages are prepended
        so that any system messages already in memory stay at the top.
        """
        records = await persistence.load_messages(conversation_id)
        if not records:
            return
        loaded = [Message.from_persistent(r) for r in records]
        with self._lock:
            # Insert loaded messages after existing system messages.
            system_msgs = [m for m in self.messages if m.role == Role.SYSTEM]
            rest = [m for m in self.messages if m.role != Role.SYSTEM]
            self.messages = system_msgs + loaded + rest
            self._trim()
        logger.info(
            "memory_load_from_db conversation_id=%s loaded=%d total=%d",
            conversation_id,
            len(loaded),
            len(self.messages),
        )

    async def save_to_db(
        self,
        persistence: PersistencePort,
        conversation_id: str,
    ) -> None:
        """Persist the current message list through the persistence layer."""
        payload = [message.to_persistent() for message in self.snapshot()]
        if not payload:
            return
        await persistence.save_messages(conversation_id, payload)
        logger.info(
            "memory_save_to_db conversation_id=%s count=%d",
            conversation_id,
            len(payload),
        )

    # Internal -----------------------------------------------------------

    def _trim(self) -> None:
        kept_system = [m for m in self.messages if m.role == Role.SYSTEM]
        non_system = [m for m in self.messages if m.role != Role.SYSTEM]

        # Identify and protect skill activation message pairs (assistant tool call
        # + corresponding tool result). These are treated as atomic units and are
        # not eligible for trimming. For duplicate activations of the same skill,
        # only the most recent pair is kept.
        protected_pairs = self._extract_protected_skill_pairs(non_system)
        protected_ids = {id(msg) for pair in protected_pairs for msg in pair}
        trimmable = [m for m in non_system if id(m) not in protected_ids]

        # Apply message-count limit.
        if self.max_messages > 0 and len(self.messages) > self.max_messages:
            budget = max(0, self.max_messages - len(kept_system) - len(protected_pairs) * 2)
            trimmable = trimmable[-budget:] if budget > 0 else []

        # Apply token-budget limit.
        if self.max_tokens > 0:
            system_tokens = sum(_estimate_tokens(m.content) for m in kept_system)
            protected_tokens = sum(
                _estimate_tokens(m.content) for pair in protected_pairs for m in pair
            )
            remaining_budget = self.max_tokens - system_tokens - protected_tokens
            kept_trimmable: list[Message] = []
            used = 0
            for msg in reversed(trimmable):
                msg_tokens = _estimate_tokens(msg.content)
                if used + msg_tokens > remaining_budget:
                    break
                kept_trimmable.append(msg)
                used += msg_tokens
            trimmable = list(reversed(kept_trimmable))

        # Reassemble preserving original order among protected + trimmable messages.
        kept_non_system: list[Message] = []
        protected_iter = iter(protected_pairs)
        current_pair = next(protected_iter, None)
        for msg in non_system:
            if current_pair is not None and msg is current_pair[0]:
                kept_non_system.extend(current_pair)
                current_pair = next(protected_iter, None)
            elif msg in trimmable:
                kept_non_system.append(msg)

        self.messages = kept_system + kept_non_system

    @staticmethod
    def _extract_protected_skill_pairs(messages: list[Message]) -> list[tuple[Message, Message]]:
        """Return the most recent assistant/tool-result pair for each activated skill.

        A pair is protected when the tool result message has
        ``metadata["skill_activation"] == True``. The matching assistant message is
        the one containing the corresponding ``tool_call_id`` in its ``tool_calls``.
        """
        protected_tool_indices: list[int] = []
        for idx, msg in enumerate(messages):
            if msg.role != Role.TOOL:
                continue
            if not msg.metadata.get("skill_activation"):
                continue
            protected_tool_indices.append(idx)

        pairs: list[tuple[Message, Message]] = []
        for tool_idx in protected_tool_indices:
            tool_msg = messages[tool_idx]
            tool_call_id = tool_msg.tool_call_id
            if not tool_call_id:
                continue
            # Walk backwards to find the matching assistant tool-call message.
            assistant_msg: Message | None = None
            for prev in reversed(messages[:tool_idx]):
                if prev.role != Role.ASSISTANT or not prev.tool_calls:
                    continue
                if any(call.get("id") == tool_call_id for call in prev.tool_calls):
                    assistant_msg = prev
                    break
            if assistant_msg is None:
                continue
            pairs.append((assistant_msg, tool_msg))

        # Deduplicate by skill name, keeping only the latest activation for each.
        seen: set[str] = set()
        unique_pairs: list[tuple[Message, Message]] = []
        for assistant_msg, tool_msg in reversed(pairs):
            skill_name = str(tool_msg.metadata.get("skill_name", ""))
            if not skill_name or skill_name not in seen:
                if skill_name:
                    seen.add(skill_name)
                unique_pairs.insert(0, (assistant_msg, tool_msg))

        return unique_pairs

    def estimated_tokens(self) -> int:
        """Return the estimated total token count of all messages."""
        return sum(_estimate_tokens(m.content) for m in self.messages)
