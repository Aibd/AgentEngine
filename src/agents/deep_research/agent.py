from __future__ import annotations

from agent_core.base.agent import BaseAgent
from agent_core.registry.agent_registry import register_agent
from agent_core.skills.loader import SkillLoader


@register_agent("deep_research", handler="react")
class DeepResearchAgent(BaseAgent):
    """Deep research agent with model-native planning.

    Planning is driven entirely by the model's own reasoning — no explicit
    planning tool.  The system prompt instructs the model to decompose complex
    tasks into steps, execute them via available tools, and synthesise a final
    report.  The ReAct loop naturally handles multi-step execution.
    """

    description = "Deep research agent with model-native planning."

    def setup(self) -> None:
        # Load any skills that match the agent name.
        loader = SkillLoader()
        for skill in loader.load_for_agent(self.name):
            self.context.tool_collection.add(skill)

    def system_prompt(self) -> str:
        return (
            "你是一个深度研究助手。面对用户的请求，按以下方式工作：\n"
            "1. **分析任务**：理解用户需求，将复杂问题拆解为可执行的子任务。\n"
            "2. **逐步执行**：每一步使用可用工具获取信息，不要跳过任何步骤。\n"
            "3. **综合输出**：所有子任务完成后，整合信息给出结构化的最终报告。\n\n"
            "注意：\n"
            "- 每次只处理一个子任务，完成后再进入下一个。\n"
            "- 如果某步结果不理想，可以调整策略重新尝试。\n"
            "- 最终报告要有明确的结论和依据。"
        )

    def next_step_prompt(self) -> str:
        return (
            "继续执行下一个子任务。如果所有子任务已完成，输出最终报告。"
        )
