"""Skill lifecycle registry: import / export / enable-disable on top of the loader.

The read-only :class:`~agentengine.skills.loader.SkillLoader` stays the single
source of truth for *what a skill is* (it scans ``.agent/skills/*/SKILL.md``).
This module layers *management state* on top:

- enable / disable (without deleting files from disk),
- import a community skill packaged as a ``.zip``,
- export an installed skill back to a ``.zip``,
- delete an imported skill.

Enable state and provenance live in a tiny SQLite table (``skills_meta``);
the ``SKILL.md`` files remain authoritative for name/description/body. A skill
present on disk but absent from the table is treated as a *builtin* default
(``enabled=1``) — so skills shipped with the repo light up without a DB write.

The SQLite access pattern mirrors
``app/backend/services/reporting/db.py``: ``aiosqlite`` with a connection
opened per operation (no caching — each test runs on a fresh event loop and
``aiosqlite`` binds its worker thread to the creating loop).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator

import aiosqlite

from agentengine.skills.loader import SkillLoader

logger = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"

# Source values stored in ``skills_meta.source``.
SOURCE_BUILTIN = "builtin"
SOURCE_IMPORTED = "imported"
SOURCE_MARKET = "market"

# Sources whose skills the user may delete (builtin skills are protected).
DELETABLE_SOURCES = frozenset({SOURCE_IMPORTED, SOURCE_MARKET})


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class SkillView:
    """A skill plus its management metadata, ready for the API/UI."""

    name: str
    description: str
    enabled: bool
    source: str
    origin: str
    path: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "source": self.source,
            "origin": self.origin,
        }


@dataclass(slots=True)
class SkillDetail(SkillView):
    """:class:`SkillView` plus the SKILL.md body for the detail panel."""

    body: str

    def snapshot(self) -> dict[str, Any]:
        # Call the base implementation explicitly: a zero-arg ``super()`` inside
        # a ``slots=True`` dataclass subclass trips over the rebuilt class cell.
        data = SkillView.snapshot(self)
        data["body"] = self.body
        return data


class SkillImportError(ValueError):
    """Raised when an uploaded archive cannot be turned into a skill."""


class SkillMetadataDB:
    """SQLite store for per-skill enable state and provenance."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[aiosqlite.Connection]:
        await self._ensure_initialized()
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self._path)
            try:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS skills_meta (
                        name        TEXT PRIMARY KEY,
                        enabled     INTEGER NOT NULL DEFAULT 1,
                        source      TEXT NOT NULL DEFAULT 'builtin',
                        origin      TEXT DEFAULT '',
                        imported_at TEXT DEFAULT '',
                        updated_at  TEXT NOT NULL
                    );
                    """
                )
                await conn.commit()
            finally:
                await conn.close()
            self._initialized = True

    async def get_all(self) -> dict[str, dict[str, Any]]:
        async with self._open() as conn:
            async with conn.execute(
                "SELECT name, enabled, source, origin, imported_at, updated_at FROM skills_meta"
            ) as cursor:
                rows = await cursor.fetchall()
        return {row["name"]: dict(row) for row in rows}

    async def upsert(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        source: str | None = None,
        origin: str | None = None,
        imported_at: str | None = None,
    ) -> None:
        """Insert or update a metadata row, only touching the fields provided."""
        now = _utc_iso()
        async with self._open() as conn:
            await conn.execute(
                """
                INSERT INTO skills_meta (name, enabled, source, origin, imported_at, updated_at)
                VALUES (?, COALESCE(?, 1), COALESCE(?, 'builtin'), COALESCE(?, ''), COALESCE(?, ''), ?)
                ON CONFLICT(name) DO UPDATE SET
                    enabled     = COALESCE(excluded.enabled, skills_meta.enabled),
                    source      = COALESCE(?, skills_meta.source),
                    origin      = COALESCE(?, skills_meta.origin),
                    imported_at = COALESCE(?, skills_meta.imported_at),
                    updated_at  = excluded.updated_at
                """,
                (
                    name,
                    None if enabled is None else int(enabled),
                    source,
                    origin,
                    imported_at,
                    now,
                    source,
                    origin,
                    imported_at,
                ),
            )
            await conn.commit()

    async def delete(self, name: str) -> None:
        async with self._open() as conn:
            await conn.execute("DELETE FROM skills_meta WHERE name = ?", (name,))
            await conn.commit()


class SkillRegistry:
    """Combine a :class:`SkillLoader` with a :class:`SkillMetadataDB`.

    The registry owns the user-facing skill lifecycle. It never duplicates the
    loader's parsing — it asks the loader for what's on disk and joins that with
    enable/provenance metadata.
    """

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        db_path: str | Path,
        loader: SkillLoader | None = None,
    ) -> None:
        self._cwd = cwd or Path.cwd()
        self._loader = loader or SkillLoader(cwd=self._cwd)
        self._db = SkillMetadataDB(db_path)
        # Prefer the first existing root for imports so that existing projects
        # using ``.agent/skills/`` continue to work unchanged. If none exist,
        # default to the first configured root (standard ``.agents/skills/``).
        existing_root = next((r for r in self._loader._roots if r.is_dir()), None)
        self._install_root = existing_root or self._loader._roots[0]

    # -- listing ----------------------------------------------------------

    async def list(self) -> list[SkillView]:
        """Return every on-disk skill joined with its metadata."""
        skills = self._loader.discover(force=True)
        meta = await self._db.get_all()
        views = [self._view_for(skill, meta.get(name)) for name, skill in skills.items()]
        views.sort(key=lambda view: view.name)
        return views

    async def get(self, name: str) -> SkillDetail | None:
        """Return one skill (with body) or None if it isn't on disk."""
        skill = self._loader.discover(force=True).get(name)
        if skill is None:
            return None
        meta = (await self._db.get_all()).get(name)
        view = self._view_for(skill, meta)
        return SkillDetail(
            name=view.name,
            description=view.description,
            enabled=view.enabled,
            source=view.source,
            origin=view.origin,
            path=view.path,
            body=skill.body,
        )

    async def enabled_names(self) -> set[str]:
        """Names of skills that are currently enabled."""
        return {view.name for view in await self.list() if view.enabled}

    def _find_skill_in_dir(self, directory: Path) -> Any | None:
        """Return the discovered skill whose SKILL.md lives under *directory*."""
        target = directory.resolve()
        for skill in self._loader.discover(force=True).values():
            try:
                Path(skill.path).resolve().relative_to(target)
            except ValueError:
                continue
            return skill
        return None

    def _view_for(self, skill: Any, meta: dict[str, Any] | None) -> SkillView:
        if meta is None:
            return SkillView(
                name=skill.name,
                description=skill.description,
                enabled=True,
                source=SOURCE_BUILTIN,
                origin="",
                path=str(skill.path),
            )
        return SkillView(
            name=skill.name,
            description=skill.description,
            enabled=bool(meta["enabled"]),
            source=str(meta["source"]),
            origin=str(meta["origin"] or ""),
            path=str(skill.path),
        )

    # -- enable / disable -------------------------------------------------

    async def set_enabled(self, name: str, enabled: bool) -> SkillView:
        skill = self._loader.discover(force=True).get(name)
        if skill is None:
            raise KeyError(name)
        # Preserve existing source/origin; default to builtin on first write.
        existing = (await self._db.get_all()).get(name)
        source = str(existing["source"]) if existing else SOURCE_BUILTIN
        await self._db.upsert(name, enabled=enabled, source=source)
        meta = (await self._db.get_all()).get(name)
        return self._view_for(skill, meta)

    # -- delete -----------------------------------------------------------

    async def delete(self, name: str) -> None:
        skill = self._loader.discover(force=True).get(name)
        if skill is None:
            raise KeyError(name)
        meta = (await self._db.get_all()).get(name)
        source = str(meta["source"]) if meta else SOURCE_BUILTIN
        if source not in DELETABLE_SOURCES:
            raise PermissionError(f"skill '{name}' is built in and cannot be deleted")
        skill_dir = Path(skill.path).parent
        await asyncio.to_thread(_remove_tree, skill_dir)
        await self._db.delete(name)
        self._loader.invalidate()
        logger.info("skill_deleted name=%s dir=%s", name, skill_dir)

    # -- install / import / export ----------------------------------------

    async def install_files(
        self,
        name: str,
        files: dict[str, bytes],
        *,
        source: str,
        origin: str = "",
    ) -> SkillView:
        """Write a skill's file tree to ``.agent/skills/<name>`` and record metadata.

        Shared by ``import_zip`` (zip upload) and the skill catalog installer.
        Raises :class:`FileExistsError` (name only) when the target already
        exists, and :class:`SkillImportError` if the skill fails to load back.
        """
        target_dir = self._install_root / name
        if target_dir.exists():
            raise FileExistsError(name)
        await asyncio.to_thread(_write_skill_tree, target_dir, files)
        self._loader.invalidate()
        # The loader keys skills by their frontmatter ``name`` — which may differ
        # from the directory name we just wrote (e.g. frontmatter uses
        # underscores that the dir name normalizes to hyphens). Find the skill by
        # its on-disk path so metadata is keyed by the identity the loader uses.
        skill = self._find_skill_in_dir(target_dir)
        if skill is None:  # pragma: no cover - defensive; we just wrote it
            raise SkillImportError(f"installed skill '{name}' did not load")
        await self._db.upsert(
            skill.name,
            enabled=True,
            source=source,
            origin=origin or "",
            imported_at=_utc_iso(),
        )
        meta = (await self._db.get_all()).get(skill.name)
        logger.info("skill_installed name=%s source=%s origin=%s", skill.name, source, origin)
        return self._view_for(skill, meta)

    async def import_zip(self, data: bytes, *, original_filename: str = "") -> SkillView:
        """Install a skill from a ``.zip`` archive containing a SKILL.md."""
        name, files = await asyncio.to_thread(_extract_skill_from_zip, data)
        return await self.install_files(
            name,
            files,
            source=SOURCE_IMPORTED,
            origin=original_filename,
        )

    async def export_zip(self, name: str) -> bytes:
        """Package an installed skill directory into a ``.zip`` byte stream."""
        skill = self._loader.discover(force=True).get(name)
        if skill is None:
            raise KeyError(name)
        skill_dir = Path(skill.path).parent
        return await asyncio.to_thread(_zip_directory, skill_dir, name)


# -- module-level helpers (run in threads; no event loop needed) ----------


def _remove_tree(path: Path) -> None:
    import shutil

    if path.is_dir():
        shutil.rmtree(path)


def _safe_member_path(name: str) -> PurePosixPath | None:
    """Normalize a zip member path and reject absolute / parent-escape paths."""
    if not name or name.endswith("/"):
        return None
    pure = PurePosixPath(name)
    if pure.is_absolute():
        return None
    parts = pure.parts
    if any(part == ".." for part in parts):
        return None
    return pure


def _extract_skill_from_zip(data: bytes) -> tuple[str, dict[str, bytes]]:
    """Locate the SKILL.md, derive the skill name, and collect its file tree.

    Returns ``(normalized_name, {relative_path: content})`` where relative paths
    are rooted at the SKILL.md's directory. Raises :class:`SkillImportError` for
    malformed archives.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillImportError("无法解析 zip 文件") from exc

    with archive:
        members = [
            (info.filename, safe)
            for info in archive.infolist()
            if not info.is_dir() and (safe := _safe_member_path(info.filename)) is not None
        ]
        if not members:
            raise SkillImportError("zip 内没有可用文件")

        # Pick the shallowest SKILL.md as the skill root.
        skill_md = min(
            (path for _, path in members if path.name == SKILL_FILE),
            key=lambda p: len(p.parts),
            default=None,
        )
        if skill_md is None:
            raise SkillImportError("zip 内未找到 SKILL.md")

        root = skill_md.parent  # PurePosixPath("") when SKILL.md is top-level
        root_parts = root.parts

        files: dict[str, bytes] = {}
        for original, path in members:
            if root_parts and path.parts[: len(root_parts)] != root_parts:
                continue  # outside the skill root subtree
            rel = path.relative_to(root) if root_parts else path
            files[str(rel)] = archive.read(original)

        name = _derive_skill_name(files.get(SKILL_FILE, b""), root)
        return name, files


def _derive_skill_name(skill_md: bytes, root: PurePosixPath) -> str:
    """Frontmatter ``name`` wins; fall back to the root directory name."""
    fallback = root.name or "skill"
    try:
        frontmatter, _ = SkillLoader._split_frontmatter(skill_md.decode("utf-8"))
    except UnicodeDecodeError:
        frontmatter = None
    raw = ""
    if frontmatter:
        raw = str(frontmatter.get("name") or "")
    try:
        return SkillLoader._normalize_skill_name(raw or fallback)
    except ValueError as exc:
        raise SkillImportError("无法从 SKILL.md 解析出有效的 skill 名") from exc


def _write_skill_tree(target_dir: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        dest = target_dir / rel
        # Final guard against any path that would escape target_dir.
        resolved = dest.resolve()
        if not str(resolved).startswith(str(target_dir.resolve())):
            raise SkillImportError(f"非法的文件路径: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)


def _zip_directory(directory: Path, arc_root: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(directory.rglob("*")):
            if file_path.is_file():
                arcname = f"{arc_root}/{file_path.relative_to(directory).as_posix()}"
                archive.write(file_path, arcname)
    return buffer.getvalue()


__all__ = [
    "SkillDetail",
    "SkillImportError",
    "SkillMetadataDB",
    "SkillRegistry",
    "SkillView",
    "SOURCE_BUILTIN",
    "SOURCE_IMPORTED",
    "SOURCE_MARKET",
    "DELETABLE_SOURCES",
]
