from dataclasses import dataclass, field
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

    def append(self, message: Message) -> None:
        self.messages.append(message)
        self._trim()

    def extend(self, messages: Iterable[Message]) -> None:
        for message in messages:
            self.messages.append(message)
        self._trim()

    def clear(self) -> None:
        self.messages.clear()

    def to_openai(self) -> list[dict[str, Any]]:
        return [message.to_openai() for message in self.messages]

    # Convenience helpers ------------------------------------------------

    def add_system_message(self, content: str) -> None:
        self.append(Message.system(content))

    def add_user_message(self, content: str, *, base64_image: str | None = None) -> None:
        self.append(Message.user(content, base64_image=base64_image))

    def add_assistant_message(
        self,
        content: str = "",
        *,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.append(Message.assistant(content, tool_calls=tool_calls))

    def add_tool_message(self, content: str, *, tool_call_id: str) -> None:
        self.append(Message.tool(content, tool_call_id=tool_call_id))

    def last_user_message(self) -> str:
        for message in reversed(self.messages):
            if message.role == Role.USER:
                return message.content
        return ""

    def last_assistant_message(self) -> str:
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
