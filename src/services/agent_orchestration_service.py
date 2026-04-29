from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from agent_core.base.context import AgentContext
from agent_core.handlers.base import AgentHandler
from agent_core.llm.client import LLMClient
from agent_core.llm.factory import create_llm_from_env
from agent_core.registry.agent_registry import create_agent, get_agent_handler
from agent_core.registry.handler_registry import create_handler
from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer

# Boot-time imports register built-in handlers and sample agents.
import agent_core.handlers  # noqa: F401
import agents.deep_research  # noqa: F401
import agents.general_chat  # noqa: F401
import agents.adapters  # noqa: F401

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


DEFAULT_MAX_QUERY_CHARS = 20_000
logger = logging.getLogger(__name__)


def _default_llm_factory() -> LLMClient | None:
    return cast(LLMClient | None, create_llm_from_env(required=False))


class AgentOrchestrationService:
    """Application-facing entry point.

    Looks up agent + handler by name, optionally reading per-agent settings
    from `config/agents.yaml`. Provides a streaming context factory for SSE
    endpoints.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        llm_factory: Callable[[], LLMClient | None] | None = None,
        max_query_chars: int = DEFAULT_MAX_QUERY_CHARS,
    ) -> None:
        if max_query_chars < 1:
            raise ValueError("max_query_chars must be at least 1")
        self._config: dict[str, Any] = self._load_config(config_path)
        self._llm_factory = llm_factory or _default_llm_factory
        self._max_query_chars = max_query_chars
        self._managed_llms: dict[int, LLMClient] = {}

    def _load_config(self, config_path: str | Path | None) -> dict[str, Any]:
        if config_path is None or yaml is None:
            return {}
        path = Path(config_path)
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}

    def agent_config(self, agent_name: str) -> dict[str, Any]:
        agents_cfg = self._config.get("agents", {}) or {}
        return agents_cfg.get(agent_name, {}) or {}

    def is_enabled(self, agent_name: str) -> bool:
        cfg = self.agent_config(agent_name)
        return bool(cfg.get("enabled", True))

    async def run(
        self,
        *,
        agent_name: str,
        query: str,
        context: AgentContext | None = None,
        agent_kwargs: dict[str, Any] | None = None,
        handler_kwargs: dict[str, Any] | None = None,
    ) -> str:
        self._validate_run_inputs(agent_name=agent_name, query=query)
        if not self.is_enabled(agent_name):
            raise RuntimeError(f"Agent disabled in config: {agent_name}")

        cfg = self.agent_config(agent_name)
        merged_agent_kwargs: dict[str, Any] = {}
        if "max_steps" in cfg:
            merged_agent_kwargs["max_steps"] = cfg["max_steps"]
        merged_agent_kwargs.update(agent_kwargs or {})

        context = context or AgentContext(request_id="local", query=query)
        if context.llm is None:
            context.llm = self._llm_factory()
            self._track_managed_llm(context.llm)

        started_at = time.perf_counter()
        agent = create_agent(agent_name, context, **merged_agent_kwargs)
        context.extras["agent"] = agent
        handler_name = cfg.get("handler") or get_agent_handler(agent_name)
        handler = cast(AgentHandler, create_handler(handler_name, **(handler_kwargs or {})))
        logger.info(
            "agent_run_start request_id=%s agent=%s handler=%s conversation_id=%s",
            context.request_id,
            agent_name,
            handler_name,
            context.conversation_id,
        )
        try:
            return await handler.handle(agent, context, query)
        finally:
            context.extras["agent_state"] = agent.state.value
            context.extras["agent_current_step"] = agent.current_step
            context.extras["agent_memory"] = agent.memory.to_openai()
            logger.info(
                "agent_run_finish request_id=%s agent=%s handler=%s state=%s steps=%d elapsed=%.3fs",
                context.request_id,
                agent_name,
                handler_name,
                agent.state.value,
                agent.current_step,
                time.perf_counter() - started_at,
            )

    def _track_managed_llm(self, llm: LLMClient | None) -> None:
        if llm is None or not hasattr(llm, "close"):
            return
        self._managed_llms[id(llm)] = llm

    async def close(self) -> None:
        llms = list(self._managed_llms.values())
        self._managed_llms.clear()
        for llm in llms:
            close = getattr(llm, "close", None)
            if close is None:
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def shutdown(self) -> None:
        await self.close()

    async def __aenter__(self) -> "AgentOrchestrationService":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _validate_run_inputs(self, *, agent_name: str, query: str) -> None:
        if not agent_name or not agent_name.strip():
            raise ValueError("agent_name must not be empty")
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        if not query.strip():
            raise ValueError("query must not be empty")
        if len(query) > self._max_query_chars:
            raise ValueError(
                f"query is too long: {len(query)} > {self._max_query_chars}"
            )

    def create_streaming_context(
        self,
        *,
        request_id: str,
        query: str,
        conversation_id: str = "",
    ) -> tuple[AgentContext, EventStream]:
        event_stream = EventStream()
        printer = Printer(
            request_id=request_id,
            event_stream=event_stream,
            conversation_id=conversation_id,
        )
        context = AgentContext(
            request_id=request_id,
            query=query,
            printer=printer,
            conversation_id=conversation_id,
        )
        return context, event_stream
