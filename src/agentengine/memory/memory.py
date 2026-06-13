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
        loaded = [Message.from_openai(r) for r in records]
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
        payload = self.to_openai()
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

        # Apply message-count limit.
        if self.max_messages > 0 and len(self.messages) > self.max_messages:
            budget = max(0, self.max_messages - len(kept_system))
            non_system = non_system[-budget:] if budget > 0 else []

        # Apply token-budget limit.
        if self.max_tokens > 0:
            system_tokens = sum(_estimate_tokens(m.content) for m in kept_system)
            remaining_budget = self.max_tokens - system_tokens
            # Walk from newest to oldest, keeping as many as fit.
            kept_non_system: list[Message] = []
            used = 0
            for msg in reversed(non_system):
                msg_tokens = _estimate_tokens(msg.content)
                if used + msg_tokens > remaining_budget:
                    break
                kept_non_system.append(msg)
                used += msg_tokens
            non_system = list(reversed(kept_non_system))

        self.messages = kept_system + non_system

    def estimated_tokens(self) -> int:
        """Return the estimated total token count of all messages."""
        return sum(_estimate_tokens(m.content) for m in self.messages)
