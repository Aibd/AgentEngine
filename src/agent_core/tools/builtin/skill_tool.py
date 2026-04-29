"""Skill as a first-class Tool - Claude Code pattern."""

from __future__ import annotations

import logging
from typing import Any

from agent_core.skills.loader import SkillLoader
from agent_core.tools.base import Tool

logger = logging.getLogger(__name__)

SKILL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill": {"type": "string", "description": "Skill name"},
        "args": {"type": "string", "description": "Optional arguments"},
    },
    "required": ["skill"],
}


class SkillTool(Tool):
    name = "Skill"
    description = "Execute a named skill. Call this FIRST before doing work a skill covers."
    schema = SKILL_TOOL_SCHEMA

    def __init__(self, loader: SkillLoader) -> None:
        self._loader = loader

    async def run(self, **kwargs: Any) -> str:
        skill = str(kwargs.get("skill", ""))
        args = str(kwargs.get("args", ""))

        skills = self._loader.discover()
        if skill not in skills:
            return f"Unknown skill: {skill}. Available: {', '.join(sorted(skills)) or '(none)'}"

        loaded_skill = skills[skill]
        logger.info("skill_invoked name=%s", skill)
        prompt = loaded_skill.prompt.replace("${ARGUMENTS}", args)
        return (
            f"[Skill: {loaded_skill.name}]\n"
            f"Description: {loaded_skill.description}\n\n"
            "--- Instructions ---\n"
            f"{prompt}\n"
            "--- End ---\n\n"
            "Follow the instructions above."
        )
