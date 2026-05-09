"""Secrets provider abstraction.

``SecretsProvider`` decouples credential retrieval from environment variables
so operators can plug in HashiCorp Vault, AWS Secrets Manager, or any other
backend. The default implementation reads from ``os.environ`` for backwards
compatibility.

Usage::

    from agentengine.enterprise.secrets import EnvSecrets, SecretsProvider

    def create_llm(provider: SecretsProvider) -> LLMClient:
        key = await provider.get("LLM_API_KEY")
        # ...

Integration point:
    Inject a ``SecretsProvider`` into ``AgentContext.extras["secrets"]``.
    The LLM factory and tool implementations can look it up there.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


class SecretsProvider(ABC):
    """Abstract backend for secret retrieval."""

    @abstractmethod
    async def get(self, key: str) -> str:
        """Return the secret value for *key*, or raise ``SecretsError``."""
        ...

    @abstractmethod
    async def get_optional(self, key: str) -> str | None:
        """Return the secret value or None."""
        ...

    async def load_all(self, prefix: str = "") -> dict[str, str]:
        """Return all secrets whose key starts with *prefix*."""
        return {}


class SecretsError(Exception):
    """Raised when a required secret is missing or unreachable."""

    def __init__(self, key: str, reason: str = "") -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"Secret '{key}' not available: {reason}")


@dataclass(slots=True)
class EnvSecrets(SecretsProvider):
    """Default provider — reads from ``os.environ``."""

    async def get(self, key: str) -> str:
        value = os.getenv(key)
        if value is None:
            raise SecretsError(key, "environment variable not set")
        return value

    async def get_optional(self, key: str) -> str | None:
        return os.getenv(key)

    async def load_all(self, prefix: str = "") -> dict[str, str]:
        result: dict[str, str] = {}
        for k, v in os.environ.items():
            if k.startswith(prefix):
                result[k] = v
        return result