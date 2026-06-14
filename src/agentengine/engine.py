"""Public SDK entry point for embedding AgentEngine in host systems."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.concurrency import (
    ConversationLockManager,
    InMemoryConversationLockManager,
)
from agentengine.enterprise.middleware import MiddlewareChain
from agentengine.llm.interfaces import LLMClient
from agentengine.memory.message import Message, Role
from agentengine.persistence.port import PersistencePort
from agentengine.definition import AgentDefinition
from agentengine.run_config import RunConfig
from agentengine.skills.catalog_prompt import SkillCatalogPrompt
from agentengine.skills.loader import SkillLoader
from agentengine.runtime.events import RuntimeEvent
from agentengine.runtime.cancellation import CancellationToken
from agentengine.runtime.turn import DEFAULT_TOOL_TIMEOUT_SECONDS
from agentengine.runtime.turn_runner import TurnRunner
from agentengine.stream.printer import Printer
from agentengine.stream.sse_queue import SseEventQueue
from agentengine.stream.sse_sink import SseSink


DEFAULT_MAX_QUERY_CHARS = 20_000

EventCallback = Callable[[RuntimeEvent], Awaitable[None] | None]
EnabledNames = Callable[[], set[str]] | set[str] | None
LLMFactory = Callable[[], LLMClient | None]
ConfigResolver = Callable[[str, dict[str, Any] | None], RunConfig]
DefinitionLike = AgentDefinition | RunConfig

logger = logging.getLogger(__name__)


class SupportsRunConfig(Protocol):
    def to_run_config(self) -> RunConfig:
        ...


class AgentEngine:
    """SDK facade for running agent definitions inside another application.

    The engine owns runtime concerns: validating inputs, resolving a definition
    to ``RunConfig``, wiring persistence and locks, and forwarding RuntimeEvents.
    Host systems remain responsible for authentication, tenant routing, HTTP
    adapters, and LLM configuration.
    """

    def __init__(
        self,
        *,
        definitions: Mapping[str, DefinitionLike | SupportsRunConfig] | None = None,
        config_resolver: ConfigResolver | None = None,
        llm_factory: LLMFactory | None = None,
        require_llm: bool = True,
        max_query_chars: int = DEFAULT_MAX_QUERY_CHARS,
        persistence: PersistencePort | None = None,
        lock_manager: ConversationLockManager | None = None,
        middleware: MiddlewareChain | None = None,
        skill_loader: SkillLoader | None = None,
        enable_skill_catalog: bool = False,
        enabled_skills: EnabledNames = None,
    ) -> None:
        if max_query_chars < 1:
            raise ValueError("max_query_chars must be at least 1")
        if definitions is not None and config_resolver is not None:
            raise ValueError("pass either definitions or config_resolver, not both")
        if enable_skill_catalog and skill_loader is None:
            raise ValueError("enable_skill_catalog requires a skill_loader")
        self._definitions = dict(definitions or {})
        self._config_resolver = config_resolver
        self._llm_factory = llm_factory
        self._require_llm = require_llm
        self._max_query_chars = max_query_chars
        self._managed_llms: dict[int, LLMClient] = {}
        self._active_runs: dict[str, tuple[CancellationToken, asyncio.Task[Any] | None]] = {}
        self._persistence = persistence
        self._lock_manager = lock_manager or InMemoryConversationLockManager()
        self._middleware = middleware
        self._skill_loader = skill_loader
        self._enable_skill_catalog = enable_skill_catalog
        self._enabled_skills = enabled_skills

    async def run(
        self,
        *,
        agent_name: str,
        query: str,
        context: AgentContext | None = None,
        tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
        agent_kwargs: dict[str, Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> str:
        """Run one agent turn and return the final assistant text."""

        self._validate_run_inputs(agent_name=agent_name, query=query)
        config = self._resolve_config(agent_name, agent_kwargs=agent_kwargs)

        context = context or AgentContext(request_id="local", query=query)
        if context.llm is None and self._llm_factory is not None:
            context.llm = self._llm_factory()
            self._track_managed_llm(context.llm)
        if context.llm is None and self._require_llm:
            raise RuntimeError(
                "No LLM client configured. Pass AgentContext(llm=...), "
                "AgentEngine(llm_factory=...), or set require_llm=False."
            )
        if context.persistence is None and self._persistence is not None:
            context.persistence = self._persistence

        started_at = time.perf_counter()
        agent = AgentRun(config=config, context=context)
        if self._enable_skill_catalog and self._skill_loader is not None:
            context.extras["_inject_skill_catalog"] = self._inject_skill_catalog
        cancellation_token = CancellationToken()
        task = asyncio.current_task()
        self._active_runs[context.request_id] = (cancellation_token, task)
        context.extras["agent"] = agent
        context.extras["run_config"] = config
        context.extras["cancellation_token"] = cancellation_token
        logger.info(
            "agent_run_start request_id=%s agent=%s conversation_id=%s",
            context.request_id,
            agent_name,
            context.conversation_id,
        )

        event_callback = self._compose_event_callback(context, on_event)
        runner = TurnRunner(
            session_id=context.session_id or context.conversation_id or context.request_id,
            middleware=self._middleware,
        )
        try:
            async with self._lock_manager.acquire(context.conversation_id):
                return await runner.run(
                    agent=agent,
                    context=context,
                    query=query,
                    on_event=event_callback,
                    tool_timeout_seconds=tool_timeout_seconds,
                )
        finally:
            self._active_runs.pop(context.request_id, None)
            self._record_agent_finish(
                agent=agent,
                context=context,
                started_at=started_at,
                agent_name=agent_name,
            )

    def _inject_skill_catalog(self, agent: AgentRun) -> None:
        """Add a system message exposing enabled skills to the model.

        The message is marked with metadata so it can be deduplicated across
        turns and filtered from public API responses.
        """
        if not self._enable_skill_catalog or self._skill_loader is None:
            return

        if any(
            message.metadata.get("skill_catalog")
            for message in agent.memory.snapshot()
            if message.role == Role.SYSTEM
        ):
            return

        skills = self._skill_loader.discover().values()
        enabled = self._resolve_enabled()
        if enabled is not None:
            skills = [skill for skill in skills if skill.name in enabled]

        catalog = SkillCatalogPrompt.render(skills)
        if catalog:
            agent.memory.append(
                Message.system(
                    catalog,
                    metadata={"skill_catalog": True, "generated": True},
                )
            )

    def _resolve_enabled(self) -> set[str] | None:
        """Resolve the optional enabled-skills allow-list."""
        source = self._enabled_skills
        if source is None:
            return None
        if callable(source):
            return set(source())
        return set(source)

    def interrupt(self, request_id: str, reason: str = "interrupted") -> bool:
        """Request cancellation for an active run by request id."""

        active = self._active_runs.get(request_id)
        if active is None:
            return False
        token, task = active
        token.cancel(reason)
        if task is not None:
            task.cancel()
        return True

    def _resolve_config(
        self,
        agent_name: str,
        *,
        agent_kwargs: dict[str, Any] | None = None,
    ) -> RunConfig:
        if self._config_resolver is not None:
            config = self._config_resolver(agent_name, agent_kwargs)
        else:
            definition = self._definitions.get(agent_name)
            if definition is None:
                raise KeyError(f"Agent not registered: {agent_name}")
            config = self._definition_to_config(definition)

        kwargs = agent_kwargs or {}
        unsupported = {"max_turns", "max_steps"} & set(kwargs)
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise TypeError(f"agent_kwargs no longer supports: {names}")
        return config

    @staticmethod
    def _definition_to_config(definition: DefinitionLike | SupportsRunConfig) -> RunConfig:
        if isinstance(definition, RunConfig):
            return definition
        to_run_config = getattr(definition, "to_run_config", None)
        if to_run_config is None:
            raise TypeError("agent definitions must be RunConfig or expose to_run_config()")
        return cast(RunConfig, to_run_config())

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

    async def __aenter__(self) -> "AgentEngine":
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
    ) -> tuple[AgentContext, SseEventQueue]:
        event_stream = SseEventQueue()
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
            persistence=self._persistence,
        )
        return context, event_stream

    @staticmethod
    def _compose_event_callback(
        context: AgentContext,
        callback: EventCallback | None,
    ) -> EventCallback | None:
        sse_sink = SseSink(context.printer)
        callbacks: list[EventCallback] = []
        if context.printer is not None:
            callbacks.append(sse_sink.consume)
        if callback is not None:
            callbacks.append(callback)
        if not callbacks:
            return None

        async def _emit(event: RuntimeEvent) -> None:
            for item in callbacks:
                result = item(event)
                if inspect.isawaitable(result):
                    await result

        return _emit


# Backward-compatible alias
PresetLike = DefinitionLike


__all__ = [
    "AgentEngine",
    "ConfigResolver",
    "DEFAULT_MAX_QUERY_CHARS",
    "DefinitionLike",
    "EventCallback",
    "LLMFactory",
    "PresetLike",
]
