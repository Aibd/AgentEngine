"""Minimal skill loader - scans .agent/skills/*/SKILL.md.

Design (Claude Code pattern):
  - Single-layer scan (no deep recursion)
  - Only directory format: skill-name/SKILL.md
  - Simple in-memory cache with explicit invalidation
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """A loaded skill ready for injection into the agent context."""

    name: str
    description: str
    path: Path
    body: str  # SKILL.md body after frontmatter

    @cached_property
    def prompt(self) -> str:
        """Full prompt injected when the skill is invoked."""
        return f"Base directory: {self.path.parent}\n\n{self.body}"


class SkillLoader:
    """Scan configured roots for skill directories containing SKILL.md.

    Roots (highest priority first when deduplicating by name):
      1. Project:  .agent/skills/  (relative to cwd)
      2. User:     ~/.agent/skills/

    Cache is deliberately simple - call invalidate() when skills change on
    disk (Phase 2+ can add file watching).
    """

    SKILL_FILE = "SKILL.md"

    def __init__(self, roots: list[Path] | None = None, *, cwd: Path | None = None) -> None:
        self._roots: list[Path] = roots or self._default_roots(cwd or Path.cwd())
        self._cache: dict[str, Skill] | None = None

    def discover(self, *, force: bool = False) -> dict[str, Skill]:
        """Return all discovered skills keyed by name.

        Results are cached after the first call; pass ``force=True`` to
        re-scan the filesystem.
        """
        if self._cache is not None and not force:
            return self._cache

        skills: dict[str, Skill] = {}
        for root in self._roots:
            if not root.is_dir():
                logger.debug("skill_root_missing root=%s", root)
                continue

            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue

                skill_file = entry / self.SKILL_FILE
                if not skill_file.is_file():
                    continue

                skill = self._parse(skill_file)
                if skill is None:
                    continue

                # First root wins on name collision.
                if skill.name not in skills:
                    skills[skill.name] = skill
                    logger.debug("skill_discovered name=%s path=%s", skill.name, skill.path)

        self._cache = skills
        logger.info("skill_scan_complete count=%d roots=%d", len(skills), len(self._roots))
        return skills

    def invalidate(self) -> None:
        """Clear the cache so the next discover() re-scans disk."""
        self._cache = None
        logger.debug("skill_cache_invalidated")

    def scaffold(
        self,
        name: str,
        *,
        description: str = "",
        root: Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Create a skill directory with a starter SKILL.md and return its path."""
        skill_name = self._normalize_skill_name(name)
        target_root = root or self._roots[0]
        skill_dir = target_root / skill_name
        skill_file = skill_dir / self.SKILL_FILE

        if skill_file.exists() and not overwrite:
            raise FileExistsError(f"Skill already exists: {skill_file}")

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(
            self._render_scaffold(skill_name, description),
            encoding="utf-8",
            newline="\n",
        )
        self.invalidate()
        logger.info("skill_scaffold_created name=%s path=%s", skill_name, skill_file)
        return skill_file

    @staticmethod
    def _default_roots(cwd: Path) -> list[Path]:
        return [
            cwd / ".agent" / "skills",
            Path.home() / ".agent" / "skills",
        ]

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        normalized = name.strip().lower().replace("_", "-")
        normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        if not normalized:
            raise ValueError("Skill name must contain at least one letter or number")
        return normalized

    @staticmethod
    def _render_scaffold(name: str, description: str) -> str:
        safe_description = description.strip() or f"{name} skill"
        return (
            "---\n"
            f"name: {name}\n"
            f"description: {safe_description}\n"
            "---\n\n"
            "# Instructions\n\n"
            "Describe when to use this skill and the steps the agent should follow.\n\n"
            "Arguments: ${ARGUMENTS}\n"
        )

    def _parse(self, path: Path) -> Skill | None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("skill_read_error path=%s", path)
            return None

        frontmatter, body = self._split_frontmatter(content)
        if frontmatter is None:
            logger.warning("skill_no_frontmatter path=%s", path)
            return None

        name = frontmatter.get("name") or path.parent.name
        description = frontmatter.get("description", "")
        if "allowed-tools" in frontmatter:
            logger.warning("skill_allowed_tools_ignored path=%s", path)

        return Skill(
            name=name,
            description=description,
            path=path,
            body=body,
        )

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[dict[str, Any] | None, str]:
        """Split YAML frontmatter (--- ... ---) from body.

        Returns (None, content) when frontmatter is missing so callers can
        distinguish "no frontmatter" from "empty frontmatter".
        """
        m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)?(.*)", content, re.DOTALL)
        if not m:
            return None, content
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            logger.warning("skill_yaml_parse_error")
            return None, content
        return fm, m.group(2)
