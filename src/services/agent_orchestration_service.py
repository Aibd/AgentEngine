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


class AgentOrchestrationService:
    async def run(
        self,
        *,
        agent_name: str,
        query: str,
        context: AgentContext | None = None,
    ) -> str:
        context = context or AgentContext(request_id="local", query=query)
        agent = create_agent(agent_name, context)
        handler_name = get_agent_handler(agent_name)
        handler = create_handler(handler_name)
        return await handler.handle(agent, context, query)

    def create_streaming_context(self, *, request_id: str, query: str, conversation_id: str = "") -> tuple[AgentContext, EventStream]:
        event_stream = EventStream()
        printer = Printer(request_id=request_id, event_stream=event_stream, conversation_id=conversation_id)
        context = AgentContext(request_id=request_id, query=query, printer=printer, conversation_id=conversation_id)
        return context, event_stream
