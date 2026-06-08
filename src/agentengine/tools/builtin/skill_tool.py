"""Skill as a first-class Tool - Claude Code pattern."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from agentengine.skills.loader import SkillLoader
from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)

SKILL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "skill": {"type": "string", "description": "Skill name"},
        "args": {"type": "string", "description": "Optional arguments"},
    },
    "required": ["skill"],
}

EnabledNames = Callable[[], set[str]] | set[str] | None


class SkillTool(Tool):
    name = "Skill"
    description = "Execute a named skill. Call this FIRST before doing work a skill covers."
    schema = SKILL_TOOL_SCHEMA

    def __init__(
        self,
        loader: SkillLoader,
        *,
        enabled_names: EnabledNames = None,
    ) -> None:
        """Args:

        loader: scans ``.agent/skills`` for SKILL.md files.
        enabled_names: optional allow-list of skill names that may be invoked.
            Accepts a plain ``set`` or a zero-arg callable returning one (so the
            caller can compute it lazily/freshly). When ``None`` (the default)
            no filtering happens and every discovered skill is invocable —
            preserving the original behaviour.
        """
        self._loader = loader
        self._enabled_names = enabled_names

    def _resolve_enabled(self) -> set[str] | None:
        source = self._enabled_names
        if source is None:
            return None
        if callable(source):
            return set(source())
        return set(source)

    async def run(self, **kwargs: Any) -> str:
        skill = str(kwargs.get("skill", ""))
        args = str(kwargs.get("args", ""))

        skills = self._loader.discover()
        if skill not in skills:
            return f"Unknown skill: {skill}. Available: {', '.join(sorted(skills)) or '(none)'}"

        enabled = self._resolve_enabled()
        if enabled is not None and skill not in enabled:
            logger.info("skill_invocation_blocked name=%s reason=disabled", skill)
            return (
                f"Skill is disabled: {skill}. "
                "Ask the user to enable it in the Skills manager before using it."
            )

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
