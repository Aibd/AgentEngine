from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from agent_core.base.agent import AgentRun
from agent_core.base.context import AgentContext
from agent_core.llm.client import LLMClient
from agent_core.llm.factory import create_llm_from_env
from agent_core.runtime.events import RuntimeEvent, TextDelta
from agent_core.runtime.turn import DEFAULT_TOOL_TIMEOUT_SECONDS
from agent_core.runtime.turn_runner import TurnRunner
from agent_core.spec import AgentSpec
from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer
from agents import REGISTRY as AGENT_REGISTRY

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

    Looks up an `AgentSpec` by name (optionally reading per-agent settings
    from `config/agents.yaml`), constructs an `AgentRun`, and dispatches it
    through `TurnRunner` → `run_turn()`. Provides a streaming context factory
    for SSE endpoints.
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
        tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
    ) -> str:
        self._validate_run_inputs(agent_name=agent_name, query=query)
        if not self.is_enabled(agent_name):
            raise RuntimeError(f"Agent disabled in config: {agent_name}")

        spec = self._resolve_spec(agent_name)

        context = context or AgentContext(request_id="local", query=query)
        if context.llm is None:
            context.llm = self._llm_factory()
            self._track_managed_llm(context.llm)

        started_at = time.perf_counter()
        agent = AgentRun(spec=spec, context=context)
        context.extras["agent"] = agent
        context.extras["agent_spec"] = spec
        logger.info(
            "agent_run_start request_id=%s agent=%s conversation_id=%s",
            context.request_id,
            agent_name,
            context.conversation_id,
        )

        async def on_runtime_event(event: RuntimeEvent) -> None:
            # `run_turn()` drives the public SSE stream itself; only forward
            # runtime events that aren't already represented by its output.
            if context.printer and isinstance(event, TextDelta):
                await context.printer.from_runtime_event(event)

        runner = TurnRunner(
            session_id=context.session_id or context.conversation_id or context.request_id
        )
        try:
            return await runner.run(
                agent=agent,
                context=context,
                query=query,
                on_event=on_runtime_event,
                tool_timeout_seconds=tool_timeout_seconds,
            )
        finally:
            self._record_agent_finish(
                agent=agent,
                context=context,
                started_at=started_at,
                agent_name=agent_name,
            )

    def _resolve_spec(self, agent_name: str) -> AgentSpec:
        spec = AGENT_REGISTRY.get(agent_name)
        if spec is None:
            raise KeyError(f"Agent not registered: {agent_name}")
        cfg = self.agent_config(agent_name)
        max_steps = cfg.get("max_steps")
        if max_steps is not None and max_steps != spec.max_steps:
            return replace(spec, max_steps=int(max_steps))
        return spec

    def _record_agent_finish(
        self,
        *,
        agent: Any,
        context: AgentContext,
        started_at: float,
        agent_name: str,
    ) -> None:
        context.extras["agent_state"] = agent.state.value
        context.extras["agent_current_step"] = agent.current_step
        context.extras["agent_memory"] = agent.memory.to_openai()
        logger.info(
            "agent_run_finish request_id=%s agent=%s state=%s steps=%d elapsed=%.3fs",
            context.request_id,
            agent_name,
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
