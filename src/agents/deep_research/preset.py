from agentengine.base.context import AgentContext
from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin.read_file_tool import ReadFileTool
from agentengine.tools.builtin.skill_tool import SkillTool
from agents.preset import AgentPreset

_INSTRUCTIONS = (
    "You are a deep research assistant. Break the task into clear questions, "
    "use tools to gather evidence, and then synthesize a grounded answer.\n"
    "Requirements:\n"
    "1. Start by clarifying the research plan and key unknowns.\n"
    "2. Use tools when repository files or external context are needed.\n"
    "3. Do not guess when evidence is missing; gather more context instead.\n"
    "4. Keep the final answer concise, structured, and tied to evidence.\n\n"
    "Available tools:\n"
    "- `read_file`: read repository files.\n"
    "- `Skill`: check for a task-specific workflow."
)


async def _setup(context: AgentContext) -> None:
    if context.tool_collection is None:
        return
    if context.tool_collection.get("read_file") is None:
        context.tool_collection.add(ReadFileTool())
    if context.tool_collection.get("Skill") is None:
        context.tool_collection.add(SkillTool(SkillLoader()))


PRESET = AgentPreset(
    name="deep_research",
    description="Deep research agent with model-native planning.",
    instructions=_INSTRUCTIONS,
    setup=_setup,
)
