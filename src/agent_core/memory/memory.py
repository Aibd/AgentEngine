from dataclasses import dataclass, field
from typing import Iterable

from agent_core.memory.message import Message


@dataclass
class Memory:
    messages: list[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def extend(self, messages: Iterable[Message]) -> None:
        self.messages.extend(messages)

    def clear(self) -> None:
        self.messages.clear()

    def to_openai(self) -> list[dict]:
        return [message.to_openai() for message in self.messages]
