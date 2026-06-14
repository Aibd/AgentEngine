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
    LLM verbatim on the next turn. `reasoning_content` preserves DeepSeek-style
    thinking output that some providers require on the follow-up tool turn.
    `base64_image` enables multimodal user input.
    """

    role: Role
    content: str = ""
    reasoning_content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    base64_image: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role.value}
        if self.base64_image and self.role == Role.USER:
            image_url = (
                self.base64_image
                if self.base64_image.startswith("data:")
                else f"data:image/jpeg;base64,{self.base64_image}"
            )
            payload["content"] = [
                {"type": "text", "text": self.content},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        else:
            payload["content"] = self.content
        if self.reasoning_content:
            payload["reasoning_content"] = self.reasoning_content
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        return payload

    @classmethod
    def system(cls, content: str, *, metadata: dict[str, Any] | None = None) -> "Message":
        return cls(Role.SYSTEM, content, metadata=metadata or {})

    @classmethod
    def user(
        cls,
        content: str,
        *,
        base64_image: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        return cls(Role.USER, content, base64_image=base64_image, metadata=metadata or {})

    @classmethod
    def assistant(
        cls,
        content: str = "",
        *,
        reasoning_content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        return cls(
            Role.ASSISTANT,
            content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            metadata=metadata or {},
        )

    @classmethod
    def tool(
        cls,
        content: str,
        tool_call_id: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        return cls(
            Role.TOOL,
            content,
            tool_call_id=tool_call_id,
            metadata=metadata or {},
        )

    def to_persistent(self) -> dict[str, Any]:
        """Serialize the full message for persistence, including metadata."""
        return {
            "role": self.role.value,
            "content": self.content,
            "reasoning_content": self.reasoning_content,
            "name": self.name,
            "tool_call_id": self.tool_call_id,
            "tool_calls": self.tool_calls,
            "base64_image": self.base64_image,
            "metadata": self.metadata,
        }

    @classmethod
    def from_openai(cls, data: dict[str, Any]) -> "Message":
        """Reconstruct a Message from an OpenAI-format dict (as produced by ``to_openai``)."""
        role = Role(data.get("role", "user"))
        content = data.get("content", "")
        # Multimodal content is a list; extract the text part.
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = "".join(text_parts)
        return cls(
            role=role,
            content=content or "",
            reasoning_content=data.get("reasoning_content", ""),
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=data.get("tool_calls"),
            base64_image=None,  # not persisted
            metadata=data.get("metadata") or {},
        )

    @classmethod
    def from_persistent(cls, data: dict[str, Any]) -> "Message":
        """Reconstruct a Message from a persistent dict (as produced by ``to_persistent``)."""
        role = Role(data.get("role", "user"))
        content = data.get("content", "")
        # Multimodal content is a list; extract the text part.
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = "".join(text_parts)
        return cls(
            role=role,
            content=content or "",
            reasoning_content=data.get("reasoning_content", ""),
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=data.get("tool_calls"),
            base64_image=data.get("base64_image"),
            metadata=data.get("metadata") or {},
        )
