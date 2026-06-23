"""Backward-compatible re-exports — implementation has moved to agentengine.settings."""

from agentengine.settings import LLMConfigError, create_llm_from_env

__all__ = ["LLMConfigError", "create_llm_from_env"]
