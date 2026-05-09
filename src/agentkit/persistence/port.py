from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PersistencePort(Protocol):
    """Abstract persistence interface for the agent framework.

    Implementations live outside the agent module (e.g. SQLAlchemy-based)
    and are injected via ``AgentContext.persistence``.
    """

    async def save_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        agent_name: str,
        input_msg: str,
        reply_msg: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist the final result of an agent run."""
        ...

    async def save_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Persist a batch of messages for a conversation."""
        ...

    async def load_messages(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        """Load all messages for a conversation, ordered by creation time."""
        ...

    async def save_artifact(
        self,
        run_id: str,
        artifact_type: str,
        data: dict[str, Any],
    ) -> None:
        """Persist a tool-generated artifact (e.g. search results)."""
        ...
