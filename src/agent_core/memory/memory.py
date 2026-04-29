from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Iterable

from agent_core.memory.message import Message, Role


@dataclass
class Memory:
    """Ordered message store with optional bounded size.

    `max_messages = 0` disables trimming (unbounded). When trimming kicks in,
    system messages are preserved and the oldest non-system messages are dropped.
    """

    messages: list[Message] = field(default_factory=list)
    max_messages: int = 0
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

    def add_tool_message(self, content: str, *, tool_call_id: str) -> None:
        self.append(Message.tool(content, tool_call_id=tool_call_id))

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

    # Internal -----------------------------------------------------------

    def _trim(self) -> None:
        if self.max_messages <= 0 or len(self.messages) <= self.max_messages:
            return
        kept_system = [m for m in self.messages if m.role == Role.SYSTEM]
        non_system = [m for m in self.messages if m.role != Role.SYSTEM]
        budget = max(0, self.max_messages - len(kept_system))
        non_system = non_system[-budget:] if budget > 0 else []
        self.messages = kept_system + non_system
