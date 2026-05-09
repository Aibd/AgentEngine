from agentengine.base.context import AgentContext
from agentengine.skills.loader import SkillLoader
from agentengine.spec import AgentSpec
from agentengine.tools.builtin.read_file_tool import ReadFileTool
from agentengine.tools.builtin.skill_tool import SkillTool

_SYSTEM_PROMPT = (
    "你是一个深度研究助手。面对用户的请求，按以下方式工作：\n"
    "1. **分析任务**：理解用户需求，将复杂问题拆解为可执行的子任务。\n"
    "2. **逐步执行**：每一步使用可用工具获取信息，不要跳过任何步骤。\n"
    "3. **综合输出**：所有子任务完成后，整合信息给出结构化的最终报告。\n\n"
    "可用工具：\n"
    "- `read_file`：读取工作区下的文本文件（源码、配置、文档等）。\n"
    "- `Skill`：调用预定义的研究流程，先 `Skill` 看是否有匹配技能再决定下一步。\n\n"
    "注意：\n"
    "- 每次只处理一个子任务，完成后再进入下一个。\n"
    "- 如果某步结果不理想，可以调整策略重新尝试。\n"
    "- 最终报告要有明确的结论和依据。"
)

_NEXT_STEP_PROMPT = "继续执行下一个子任务。如果所有子任务已完成，输出最终报告。"


async def _setup(context: AgentContext) -> None:
    if context.tool_collection is None:
        return
    if context.tool_collection.get("read_file") is None:
        context.tool_collection.add(ReadFileTool())
    if context.tool_collection.get("Skill") is None:
        context.tool_collection.add(SkillTool(SkillLoader()))


SPEC = AgentSpec(
    name="deep_research",
    description="Deep research agent with model-native planning.",
    system_prompt=_SYSTEM_PROMPT,
    next_step_prompt=_NEXT_STEP_PROMPT,
    setup=_setup,
)
