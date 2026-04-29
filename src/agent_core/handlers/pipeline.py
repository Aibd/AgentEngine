import asyncio
import logging
from collections.abc import Awaitable, Callable

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.base.state import AgentState
from agent_core.errors import error_to_dict
from agent_core.handlers.base import AgentHandler
from agent_core.memory.message import Message
from agent_core.registry.handler_registry import register_handler
from agent_core.stream.events import EventType

PipelineStep = Callable[[AgentContext, str], Awaitable[str]]
logger = logging.getLogger(__name__)


@register_handler("pipeline")
class PipelineHandler(AgentHandler):
    """Run a sequence of async steps; each receives the previous step's output.

    Used for fixed workflows (e.g. FileClerk) where a ReAct loop is overkill.
    If no steps are injected into the handler, the agent can provide steps via
    its `pipeline_steps()` hook.
    """

    name = "pipeline"

    def __init__(self, steps: list[PipelineStep] | None = None) -> None:
        self.steps = steps

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        primary_error: BaseException | None = None

        try:
            agent.setup()
            agent.state = AgentState.RUNNING

            if context.printer:
                await context.printer.send(EventType.START, query)

            agent.memory.append(Message.user(query))
            result = query
            steps = self.steps if self.steps is not None else agent.pipeline_steps()
            for step in steps:
                result = await step(context, result)
                agent.current_step += 1
            agent.state = AgentState.FINISHED

            if context.printer:
                await context.printer.send(EventType.RESULT, {"result": result}, finished=True)
            return result
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
