from agentengine.base.context import AgentContext
from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin.skill_tool import SkillTool
from app.backend.agents.definition import AgentDefinition

_INSTRUCTIONS = (
    "You are a deep research assistant. Break the task into clear questions, "
    "use tools to gather evidence, and then synthesize a grounded answer.\n"
    "Requirements:\n"
    "1. Start by clarifying the research plan and key unknowns.\n"
    "2. Use tools when repository files or external context are needed.\n"
    "3. Do not guess when evidence is missing; gather more context instead.\n"
    "4. Keep the final answer concise, structured, and tied to evidence.\n"
    "5. Treat the system current date/time as authoritative today. For recent "
    "news or current topics, search with the real year/date and cite tool "
    "results — never invent timelines from training knowledge.\n\n"
    "Available tools:\n"
    "- `bash` / `python`: run code to gather and process information.\n"
    "- `Skill`: check for a task-specific workflow."
)


async def _setup(context: AgentContext) -> None:
    if context.tool_collection is None:
        return
    if context.tool_collection.get("Skill") is None:
        context.tool_collection.add(SkillTool(SkillLoader()))


DEFINITION = AgentDefinition(
    name="deep_research",
    description="Deep research agent with model-native planning.",
    instructions=_INSTRUCTIONS,
    setup=_setup,
)
