from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from agentengine.base.agent import AgentRun
from agentengine.base.context import AgentContext
from agentengine.concurrency import (
    ConversationLockManager,
    InMemoryConversationLockManager,
)
from agentengine.llm.client import LLMClient
from agentengine.llm.factory import create_llm_from_env
from agentengine.persistence.port import PersistencePort
from agentengine.enterprise.middleware import MiddlewareChain
from agentengine.runtime.events import RuntimeEvent
from agentengine.runtime.turn import DEFAULT_TOOL_TIMEOUT_SECONDS
from agentengine.runtime.turn_runner import TurnRunner
from agentengine.spec import AgentSpec
from agentengine.stream.printer import Printer
from agentengine.stream.sse_queue import SseEventQueue
from agentengine.stream.sse_sink import SseSink
from agents import REGISTRY as AGENT_REGISTRY


DEFAULT_MAX_QUERY_CHARS = 20_000
logger = logging.getLogger(__name__)


def _default_llm_factory() -> LLMClient | None:
    return cast(LLMClient | None, create_llm_from_env(required=False))


class AgentOrchestrationService:
    """Application-facing entry point.

    Looks up an `AgentSpec` by name, constructs an `AgentRun`, and dispatches
    it through `TurnRunner` -> `run_turn()`. Provides a streaming context
    factory for SSE endpoints.
    """

    def __init__(
        self,
        *,
        llm_factory: Callable[[], LLMClient | None] | None = None,
        max_query_chars: int = DEFAULT_MAX_QUERY_CHARS,
        persistence: PersistencePort | None = None,
        lock_manager: ConversationLockManager | None = None,
        middleware: MiddlewareChain | None = None,
    ) -> None:
        if max_query_chars < 1:
            raise ValueError("max_query_chars must be at least 1")
        self._llm_factory = llm_factory or _default_llm_factory
        self._max_query_chars = max_query_chars
        self._managed_llms: dict[int, LLMClient] = {}
        self._persistence = persistence
        self._lock_manager = lock_manager or InMemoryConversationLockManager()
        self._middleware = middleware

    async def run(
        self,
        *,
        agent_name: str,
        query: str,
        context: AgentContext | None = None,
        tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
        agent_kwargs: dict[str, Any] | None = None,
    ) -> str:
        self._validate_run_inputs(agent_name=agent_name, query=query)
        spec = self._resolve_spec(agent_name, agent_kwargs=agent_kwargs)

        context = context or AgentContext(request_id="local", query=query)
        if context.llm is None:
            context.llm = self._llm_factory()
            self._track_managed_llm(context.llm)
        if context.persistence is None and self._persistence is not None:
            context.persistence = self._persistence

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

        sse_sink = SseSink(context.printer)

        async def on_runtime_event(event: RuntimeEvent) -> None:
            await sse_sink.consume(event)

        runner = TurnRunner(
            session_id=context.session_id or context.conversation_id or context.request_id,
            middleware=self._middleware,
        )
        try:
            # Serialize concurrent runs that share a conversation_id so their
            # memory load → run → save cycles cannot interleave and lose turns.
            async with self._lock_manager.acquire(context.conversation_id):
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

    def _resolve_spec(
        self,
        agent_name: str,
        *,
        agent_kwargs: dict[str, Any] | None = None,
    ) -> AgentSpec:
        spec = AGENT_REGISTRY.get(agent_name)
        if spec is None:
            raise KeyError(f"Agent not registered: {agent_name}")
        kwargs = agent_kwargs or {}
        max_turns = kwargs.get("max_turns")
        if max_turns is None:
            max_turns = kwargs.get("max_steps")
        if max_turns is not None and max_turns != spec.effective_max_turns:
            return replace(spec, max_turns=int(max_turns), max_steps=None)
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
