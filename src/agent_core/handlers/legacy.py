import asyncio
import logging
from typing import Any, Callable

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.errors import error_to_dict
from agent_core.handlers.base import AgentHandler
from agent_core.registry.handler_registry import register_handler
from agent_core.stream.events import EventType

logger = logging.getLogger(__name__)


@register_handler("legacy")
class LegacyHandler(AgentHandler):
    """Wrap an existing legacy agent class so it can be invoked through the
    new orchestration entry point.

    Inject a `factory(context) -> legacy_agent` that returns an object with
    a `run(query)` method. Both sync and async legacy implementations are
    accepted.
    """

    name = "legacy"

    def __init__(self, factory: Callable[[AgentContext], Any] | None = None) -> None:
        self.factory = factory

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        primary_error: BaseException | None = None

        try:
            agent.setup()
            agent.state = AgentState.RUNNING

            if context.printer:
                await context.printer.send(EventType.START, query)

            if self.factory is None:
                result: Any = ""
            else:
                legacy = self.factory(context)
                result = legacy.run(query)
                if hasattr(result, "__await__"):
                    result = await result
            agent.state = AgentState.FINISHED

            if context.printer:
                await context.printer.send(EventType.RESULT, {"result": result}, finished=True)
            return str(result) if result is not None else ""
        except asyncio.CancelledError as exc:
            primary_error = exc
            agent.state = AgentState.CANCELLED
            raise
        except Exception as exc:
            primary_error = exc
            agent.state = AgentState.ERROR
            if context.printer:
                await context.printer.send(EventType.ERROR, error_to_dict(exc), finished=True)
            raise
        finally:
            await self._teardown(agent, context, primary_error)

    async def _teardown(
        self,
        agent: BaseAgent,
        context: AgentContext,
        primary_error: BaseException | None,
    ) -> None:
        try:
            await agent.teardown()
        except Exception as exc:
            logger.exception(
                "agent_teardown_error request_id=%s agent=%s",
                context.request_id,
                agent.name,
            )
            if primary_error is None:
                agent.state = AgentState.ERROR
                if context.printer:
                    await context.printer.send(
                        EventType.ERROR,
                        error_to_dict(exc),
                        finished=True,
                    )
                raise
