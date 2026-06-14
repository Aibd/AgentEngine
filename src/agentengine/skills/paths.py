"""Path validation for skill resources and scripts.

All resource paths inside a skill are expressed as POSIX relative paths from the
skill directory root. This module centralises the rules for rejecting paths that
escape the skill directory or refer to undeclared resources.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from agentengine.skills.loader import Skill


class SkillPathError(ValueError):
    """Raised when a requested skill path is invalid or unsafe."""


def validate_skill_resource_path(
    skill: Skill,
    resource_path: str,
) -> Path:
    """Validate that *resource_path* refers to a declared resource in *skill*.

    The path must:
      - be a non-empty POSIX relative path
      - not contain ``..`` or absolute components
      - resolve to a location inside the skill's ``base_dir``
      - be listed in ``skill.resources``

    Returns the absolute :class:`Path` to the resource on disk.
    """
    if not resource_path:
        raise SkillPathError("resource path must not be empty")

    pure = PurePosixPath(resource_path)
    if pure.is_absolute():
        raise SkillPathError(f"absolute paths are not allowed: {resource_path}")
    if any(part == ".." for part in pure.parts):
        raise SkillPathError(f"paths containing '..' are not allowed: {resource_path}")
    if pure.name.startswith("."):
        raise SkillPathError(f"hidden files are not allowed: {resource_path}")

    if skill.base_dir is None:
        raise SkillPathError("skill has no base directory")

    target = (skill.base_dir / resource_path).resolve()
    try:
        target.relative_to(skill.base_dir.resolve())
    except ValueError as exc:
        raise SkillPathError(
            f"path escapes skill directory: {resource_path}"
        ) from exc

    allowed = {
        *skill.resources.scripts,
        *skill.resources.references,
        *skill.resources.assets,
    }
    if resource_path not in allowed:
        raise SkillPathError(
            f"path is not a declared resource of skill '{skill.name}': {resource_path}"
        )

    if not target.is_file():
        raise SkillPathError(f"resource does not exist: {resource_path}")

    return target
