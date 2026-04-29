"""Create LLM clients from environment variables."""

from __future__ import annotations

import os

from agent_core.llm.openai_compat import OpenAICompatibleClient


class LLMConfigError(ValueError):
    """Raised when required LLM environment variables are missing or invalid."""


def create_llm_from_env(
    *,
    prefix: str = "LLM_",
    required: bool = True,
) -> OpenAICompatibleClient | None:
    """Create an OpenAI-compatible client from environment variables.

    Required variables:
    - LLM_API_KEY
    - LLM_MODEL

    Optional variables:
    - LLM_BASE_URL (defaults to DeepSeek's OpenAI-compatible endpoint host)
    - LLM_TIMEOUT
    - LLM_MAX_RETRIES

    The chat path intentionally uses OpenAICompatibleClient's default.
    """

    api_key = os.getenv(f"{prefix}API_KEY")
    model = os.getenv(f"{prefix}MODEL")

    missing = [
        name
        for name, value in (
            (f"{prefix}API_KEY", api_key),
            (f"{prefix}MODEL", model),
        )
        if not value
    ]
    if missing:
        if not required:
            return None
        raise LLMConfigError(f"Missing LLM environment variables: {', '.join(missing)}")

    timeout = _float_env(f"{prefix}TIMEOUT", default=120.0)
    max_retries = _int_env(f"{prefix}MAX_RETRIES", default=2)

    return OpenAICompatibleClient(
        base_url=os.getenv(f"{prefix}BASE_URL", "https://api.deepseek.com"),
        api_key=api_key or "",
        model=model or "",
        timeout=timeout,
        max_retries=max_retries,
    )


def _float_env(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be a float") from exc


def _int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be an integer") from exc
