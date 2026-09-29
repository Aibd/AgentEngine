"""Build the enabled-skill catalog for model disclosure.

This module implements the "Catalog" tier of Agent Skills progressive disclosure:
at session start the model learns the name and description of every enabled skill,
without paying the token cost of full instructions. When a task matches a skill's
description, the model activates it via the ``Skill`` tool.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Iterable

from agentengine.skills.loader import Skill


@dataclass(frozen=True, slots=True)
class SkillCatalogPrompt:
    """Render a compact catalog of enabled skills."""

    @staticmethod
    def render(skills: Iterable[Skill]) -> str:
        """Return an XML catalog of *skills*, or an empty string if none.

        Only ``name`` and ``description`` are exposed, keeping the base context
        small. The result is safe to inject into a system prompt.
        """
        items = sorted(skills, key=lambda skill: skill.name)
        if not items:
            return ""

        lines = ["<available_skills>"]
        for skill in items:
            name = html.escape(skill.name)
            description = html.escape(skill.description)
            lines.append("  <skill>")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{description}</description>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        lines.append("")
        lines.append(
            "When a task matches one of the skills above, activate it by calling "
            'the Skill tool with {"skill": "<name>"} before doing the work. '
            "Skill names are NOT tools — do not invent a function named after a "
            "skill (for example, never call web-search or web-fetch as tools). "
            "After Skill activation, run bundled scripts with RunSkillScript."
        )
        return "\n".join(lines)
