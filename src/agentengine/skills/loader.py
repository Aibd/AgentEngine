"""Minimal skill loader - scans .agent/skills/*/SKILL.md.

Design (Claude Code pattern):
  - Single-layer scan (no deep recursion)
  - Only directory format: skill-name/SKILL.md
  - Simple in-memory cache with explicit invalidation
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillResources:
    """Bundled resources discovered inside a skill directory."""

    scripts: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()


class SkillResourceLimitError(ValueError):
    """Raised when a skill's bundled resources exceed the configured maximum."""


@dataclass
class Skill:
    """A loaded skill ready for injection into the agent context."""

    name: str
    description: str
    path: Path
    body: str  # SKILL.md body after frontmatter
    base_dir: Path | None = None
    resources: SkillResources | None = None
    host_exec: bool = False  # When True, scripts run on the host process (not sandbox)

    MAX_RESOURCE_FILES: int = 100
    RESOURCE_DIRS: frozenset[str] = frozenset({"scripts", "references", "assets"})

    def __post_init__(self) -> None:
        # Backfill base_dir from path when not provided (keeps existing callers working).
        if self.base_dir is None:
            object.__setattr__(self, "base_dir", self.path.parent)
        if self.resources is None and self.base_dir is not None:
            object.__setattr__(
                self,
                "resources",
                self._list_resources(self.base_dir, max_files=self.MAX_RESOURCE_FILES),
            )

    @cached_property
    def prompt(self) -> str:
        """Full prompt injected when the skill is invoked.

        Kept for backwards compatibility. New code should prefer
        :meth:`activation_content` for Agent Skills standard output.
        """
        return f"Base directory: {self.path.parent}\n\n{self.body}"

    def activation_content(self, args: str = "") -> str:
        """Return a structured XML activation block following Agent Skills conventions.

        The content includes the skill directory, instructions (with ``${ARGUMENTS}``
        replaced), and a manifest of bundled resources. Resource contents are not
        eagerly loaded.
        """
        base = str(self.path.parent)
        name = html.escape(self.name)
        description = html.escape(self.description)
        instructions = self.body.replace("${ARGUMENTS}", args)
        instructions = html.escape(instructions)

        resources = self.resources or SkillResources()

        def _resource_section(tag: str, paths: tuple[str, ...]) -> str:
            if not paths:
                return f"    <{tag}/>"
            lines = [f"    <{tag}>"]
            for path in paths:
                lines.append(f"      <file>{html.escape(path)}</file>")
            lines.append(f"    </{tag}>")
            return "\n".join(lines)

        return (
            f'<skill_content name="{name}" description="{description}">\n'
            f"  <skill_directory>{html.escape(base)}</skill_directory>\n"
            "  <instructions>\n"
            f"{instructions}\n"
            "  </instructions>\n"
            "  <skill_resources>\n"
            f"{_resource_section('scripts', resources.scripts)}\n"
            f"{_resource_section('references', resources.references)}\n"
            f"{_resource_section('assets', resources.assets)}\n"
            "  </skill_resources>\n"
            f"  <note>Relative paths in this skill are relative to: {html.escape(base)}</note>\n"
            "</skill_content>"
        )

    @classmethod
    def _list_resources(cls, skill_dir: Path, *, max_files: int = 100) -> SkillResources:
        """Discover bundled resources under ``scripts/``, ``references/`` and ``assets/``.

        Returns stable, sorted POSIX relative paths. Hidden files, cache directories
        and ``SKILL.md`` itself are ignored. Paths that escape ``skill_dir`` after
        resolving symlinks are rejected.
        """
        scripts: list[str] = []
        references: list[str] = []
        assets: list[str] = []

        resolved_skill_dir = skill_dir.resolve()
        total = 0
        limit_reached = False

        for subdir_name in sorted(cls.RESOURCE_DIRS):
            if limit_reached:
                break
            subdir = skill_dir / subdir_name
            if not subdir.is_dir():
                continue
            for path in sorted(subdir.rglob("*")):
                if limit_reached:
                    break
                if not path.is_file():
                    continue
                if path.name.startswith("."):
                    continue
                if path.name == "SKILL.md":
                    continue
                # Skip common cache / metadata directories.
                if any(part.startswith(".") or part in {"__pycache__", "node_modules"} for part in path.relative_to(skill_dir).parts):
                    continue
                try:
                    resolved = path.resolve()
                    resolved.relative_to(resolved_skill_dir)
                except (ValueError, OSError):
                    logger.warning(
                        "skill_resource_escape_rejected skill_dir=%s path=%s",
                        skill_dir,
                        path,
                    )
                    continue

                total += 1
                if total > max_files:
                    raise SkillResourceLimitError(
                        f"Skill at {skill_dir} exceeds the maximum of {max_files} resource files"
                    )

                rel = path.relative_to(skill_dir).as_posix()
                if subdir_name == "scripts":
                    scripts.append(rel)
                elif subdir_name == "references":
                    references.append(rel)
                elif subdir_name == "assets":
                    assets.append(rel)

        return SkillResources(
            scripts=tuple(sorted(scripts)),
            references=tuple(sorted(references)),
            assets=tuple(sorted(assets)),
        )


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
        """Create a skill directory with a starter SKILL.md and return its path.

        By default the skill is written to the first configured root, which is
        the project-level ``.agents/skills/`` directory.
        """
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
        # Standard Agent Skills convention uses `.agents/skills/` for cross-client
        # interoperability; `.agent/skills/` is kept for backwards compatibility.
        return [
            cwd / ".agents" / "skills",
            cwd / ".agent" / "skills",
            Path.home() / ".agents" / "skills",
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

        # Warn when the declared name diverges from the parent directory name,
        # but keep loading with the declared name for backwards compatibility.
        expected_dir_name = self._normalize_skill_name(name)
        actual_dir_name = path.parent.name
        if actual_dir_name != expected_dir_name:
            logger.warning(
                "skill_name_directory_mismatch path=%s name=%s directory=%s",
                path,
                name,
                actual_dir_name,
            )

        host_exec = bool(frontmatter.get("host_exec", False))

        try:
            return Skill(
                name=name,
                description=description,
                path=path,
                body=body,
                base_dir=path.parent,
                host_exec=host_exec,
            )
        except SkillResourceLimitError as exc:
            logger.error("skill_resource_limit_exceeded path=%s error=%s", path, exc)
            return None

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
