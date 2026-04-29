from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.base.context import AgentContext
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


class AgentOrchestrationService:
    """Application-facing entry point.

    Looks up agent + handler by name, optionally reading per-agent settings
    from `config/agents.yaml`. Provides a streaming context factory for SSE
    endpoints.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config: dict[str, Any] = self._load_config(config_path)

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
        if not self.is_enabled(agent_name):
            raise RuntimeError(f"Agent disabled in config: {agent_name}")

        cfg = self.agent_config(agent_name)
        merged_agent_kwargs: dict[str, Any] = {}
        if "max_steps" in cfg:
            merged_agent_kwargs["max_steps"] = cfg["max_steps"]
        merged_agent_kwargs.update(agent_kwargs or {})

        context = context or AgentContext(request_id="local", query=query)
        agent = create_agent(agent_name, context, **merged_agent_kwargs)
        handler_name = cfg.get("handler") or get_agent_handler(agent_name)
        handler = create_handler(handler_name, **(handler_kwargs or {}))
        return await handler.handle(agent, context, query)

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
