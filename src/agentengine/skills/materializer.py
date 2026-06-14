"""Materialize skill packages into a per-conversation workspace.

A skill lives on the host under ``.agents/skills/<name>/``. Before a script from
that skill can run inside a sandbox, the skill files are copied into the
conversation workspace at ``.skills/<name>/``. The copy is read-only from the
skill's perspective and isolated per conversation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentengine.skills.loader import Skill

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaterializedSkill:
    """Handle to a skill copied into a conversation workspace."""

    skill_name: str
    host_path: Path
    workspace_path: str


class SkillMaterializer:
    """Copy skill files into a session workspace and track freshness."""

    SKILLS_SUBDIR = ".skills"

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root)
        self._skills_dir = self._workspace_root / self.SKILLS_SUBDIR

    def _skill_dir(self, skill_name: str) -> Path:
        if not skill_name or skill_name in (".", ".."):
            raise ValueError("skill name must not be empty or a relative path component")
        if any(sep in skill_name for sep in ("/", "\\", os.sep)):
            raise ValueError(f"skill name must not contain path separators: {skill_name}")
        target = self._skills_dir / skill_name
        self._ensure_contained(target)
        return target

    def _ensure_contained(self, path: Path) -> None:
        """Raise if *path* resolves outside the workspace skills directory."""
        resolved = path.resolve()
        base = self._skills_dir.resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"skill path escapes workspace: {path}") from exc

    @staticmethod
    def _content_hash(skill: Skill) -> str:
        """Return a hash of the skill directory tree for cache-bust detection."""
        if skill.base_dir is None:
            return ""
        hasher = hashlib.sha256()
        base = skill.base_dir.resolve()
        for root, dirs, files in os.walk(base, followlinks=False):
            # Never traverse into symlinked directories.
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for name in files:
                path = Path(root) / name
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    hasher.update(path.relative_to(base).as_posix().encode("utf-8"))
                    hasher.update(path.read_bytes())
                except OSError:
                    pass
        return hasher.hexdigest()[:16]

    def _marker_path(self, skill_name: str) -> Path:
        return self._skill_dir(skill_name) / ".materialized"

    def materialize(self, skill: Skill) -> MaterializedSkill:
        """Copy *skill* into the workspace if missing or stale.

        Returns a :class:`MaterializedSkill` describing the copy. Existing copies
        are refreshed when the source skill content hash changes.
        """
        if skill.base_dir is None:
            raise ValueError(f"skill '{skill.name}' has no base directory")

        target = self._skill_dir(skill.name)
        marker = self._marker_path(skill.name)
        current_hash = self._content_hash(skill)

        if target.is_dir() and marker.is_file():
            existing_hash = marker.read_text(encoding="utf-8").strip()
            if existing_hash == current_hash:
                logger.debug("skill_materialize_cache_hit name=%s", skill.name)
                return MaterializedSkill(
                    skill_name=skill.name,
                    host_path=target,
                    workspace_path=f"{self.SKILLS_SUBDIR}/{skill.name}",
                )

        # Remove stale copy and re-create. Treat symlinks as untrusted.
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        target.mkdir(parents=True, exist_ok=True)

        base = skill.base_dir.resolve()
        for root, dirs, files in os.walk(base, followlinks=False):
            rel_root = Path(root).relative_to(base)
            # Do not recurse into (or recreate) symlinked directories.
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for name in files:
                src = Path(root) / name
                if src.is_symlink():
                    logger.debug("skill_materialize_skip_symlink src=%s", src)
                    continue
                dst = target / rel_root / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                except OSError as exc:
                    logger.warning("skill_materialize_copy_failed src=%s err=%s", src, exc)

        marker.write_text(current_hash, encoding="utf-8")
        logger.info("skill_materialized name=%s path=%s", skill.name, target)
        return MaterializedSkill(
            skill_name=skill.name,
            host_path=target,
            workspace_path=f"{self.SKILLS_SUBDIR}/{skill.name}",
        )

    def workspace_skills_dir(self) -> Path:
        return self._skills_dir
