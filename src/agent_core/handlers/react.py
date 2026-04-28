import json

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.handlers.base import AgentHandler
from agent_core.memory.message import Message
from agent_core.registry.handler_registry import register_handler


@register_handler("react")
class ReActHandler(AgentHandler):
    name = "react"

    async def handle(self, agent: BaseAgent, context: AgentContext, query: str) -> str:
        if context.llm is None:
            return await agent.run(query)

        agent.memory.append(Message.user(query))
        final_answer = ""
        for _ in range(agent.max_steps):
            response = await context.llm.chat(
                agent.memory.messages,
                tools=context.tool_collection.to_openai_tools(),
                stream=False,
            )
            if response.content:
                final_answer = response.content
                agent.memory.append(Message.assistant(response.content))
                if context.printer:
                    await context.printer.send("tool_thought", {"tool_thought": response.content})
            if not response.tool_calls:
                break
            for call in response.tool_calls:
                tool = context.tool_collection.require(call.name)
                result = await tool.run(**call.arguments)
                rendered = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                agent.memory.append(Message.tool(rendered, tool_call_id=call.id))
                if context.printer:
                    await context.printer.send("tool_result", {"tool": call.name, "toolResult": result})
        if context.printer:
            await context.printer.send("result", {"result": "[DONE]"}, finished=True)
        return final_answer
