"""Tool for reading declared resources from a skill package."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from agentengine.skills.loader import SkillLoader
from agentengine.skills.paths import SkillPathError, validate_skill_resource_path
from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)

DEFAULT_TEXT_MAX_CHARS = 100_000
BINARY_MIME_TYPES = {
    "application/pdf",
    "application/zip",
    "application/octet-stream",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/mpeg",
    "audio/wav",
    "video/mp4",
}


class ReadSkillResource(Tool):
    """Read a resource file declared by an enabled skill."""

    name = "ReadSkillResource"
    description = (
        "Read a file bundled with a skill (references/ or assets/). "
        "Only resources declared in the skill manifest can be accessed."
    )
    schema = {
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "Skill name"},
            "path": {
                "type": "string",
                "description": "Resource path relative to the skill directory, e.g. references/FORMAT.md",
            },
        },
        "required": ["skill", "path"],
    }
    timeout_seconds = 10.0
    max_result_chars = DEFAULT_TEXT_MAX_CHARS

    def __init__(
        self,
        loader: SkillLoader,
        *,
        enabled_names: set[str] | None = None,
        text_max_chars: int = DEFAULT_TEXT_MAX_CHARS,
    ) -> None:
        self._loader = loader
        self._enabled_names = enabled_names
        self._text_max_chars = text_max_chars

    async def run(self, **kwargs: Any) -> str:
        skill_name = str(kwargs.get("skill", ""))
        resource_path = str(kwargs.get("path", ""))

        skills = self._loader.discover()
        if skill_name not in skills:
            return f"Unknown skill: {skill_name}"

        if self._enabled_names is not None and skill_name not in self._enabled_names:
            return f"Skill is disabled: {skill_name}"

        skill = skills[skill_name]
        try:
            target = validate_skill_resource_path(skill, resource_path)
        except SkillPathError as exc:
            return f"Invalid resource path: {exc}"

        mime_type, _ = mimetypes.guess_type(str(target))
        mime_type = mime_type or "application/octet-stream"

        if mime_type in BINARY_MIME_TYPES:
            return (
                f"[Binary resource: {resource_path}]\n"
                f"MIME type: {mime_type}\n"
                f"Size: {target.stat().st_size} bytes\n"
                "Use a workspace tool or the front-end viewer to consume this file."
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return (
                f"[Binary or non-UTF-8 resource: {resource_path}]\n"
                f"MIME type: {mime_type}\n"
                f"Size: {target.stat().st_size} bytes"
            )
        except OSError as exc:
            return f"Failed to read resource: {exc}"

        if len(content) > self._text_max_chars:
            truncated = content[: self._text_max_chars]
            return (
                f"{truncated}\n\n"
                f"[Resource truncated: {len(content)} characters total, "
                f"showing first {self._text_max_chars}]"
            )

        return content
