from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True)
class Message:
    """A single chat message with optional multimodal and tool-call extensions.

    `tool_calls` stores OpenAI-format raw dicts so they can be sent back to the
    LLM verbatim on the next turn. `base64_image` enables multimodal user input.
    """

    role: Role
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    base64_image: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.base64_image:
            payload["base64_image"] = self.base64_image
        return payload

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(Role.SYSTEM, content)

    @classmethod
    def user(cls, content: str, *, base64_image: str | None = None) -> "Message":
        return cls(Role.USER, content, base64_image=base64_image)

    @classmethod
    def assistant(
        cls,
        content: str = "",
        *,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> "Message":
        return cls(Role.ASSISTANT, content, tool_calls=tool_calls)

    @classmethod
    def tool(cls, content: str, tool_call_id: str | None = None) -> "Message":
        return cls(Role.TOOL, content, tool_call_id=tool_call_id)
