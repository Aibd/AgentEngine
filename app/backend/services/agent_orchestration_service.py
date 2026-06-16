"""Example compatibility wrapper around the public SDK engine.

New integrations should import ``agentengine.AgentEngine`` directly and pass
their own presets plus LLM configuration. This module remains for the local
CLI/Web examples and older tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from app.backend.agents import REGISTRY as AGENT_REGISTRY
from agentengine.engine import AgentEngine, PresetLike
from agentengine.concurrency import ConversationLockManager
from agentengine.enterprise.middleware import MiddlewareChain
from agentengine.llm.interfaces import LLMClient
from agentengine.llm.env import create_llm_from_env
from agentengine.persistence.port import PersistencePort


def _default_llm_factory() -> LLMClient | None:
    return cast(LLMClient | None, create_llm_from_env(required=False))


class AgentOrchestrationService(AgentEngine):
    """Example application service.

    This preserves the old defaults: bundled example presets are registered
    automatically and the LLM may be read from environment variables. The core
    SDK intentionally does neither by default.
    """

    def __init__(
        self,
        *,
        presets: Mapping[str, PresetLike] | None = None,
        llm_factory: Callable[[], LLMClient | None] | None = None,
        require_llm: bool = False,
        persistence: PersistencePort | None = None,
        lock_manager: ConversationLockManager | None = None,
        middleware: MiddlewareChain | None = None,
    ) -> None:
        super().__init__(
            definitions=presets or AGENT_REGISTRY,
            llm_factory=llm_factory or _default_llm_factory,
            require_llm=require_llm,
            persistence=persistence,
            lock_manager=lock_manager,
            middleware=middleware,
        )


__all__ = ["AgentOrchestrationService", "_default_llm_factory"]
