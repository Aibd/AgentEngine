"""Skill marketplace catalog: browse available skills and install them locally.

The catalog is the "store front" half of skill management. It is intentionally
decoupled from where skills come from via :class:`SkillCatalogProvider`:

- :class:`LocalCatalogProvider` reads a bundled directory (``catalog_data/``)
  — works offline, ships a curated starter set.
- A future ``RemoteCatalogProvider`` (e.g. SkillHub) implements the same
  protocol; swapping it in requires no changes to :class:`SkillCatalog`, the
  web API, or the frontend.

Installing an entry hands its files to :class:`SkillRegistry.install_files`,
after which the *installed* skill is owned by the registry (enable/disable,
export, delete) exactly like a zip-imported one. The catalog only knows about
*available* entries and whether each is already installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Protocol

from agentengine.skills.loader import SkillLoader
from agentengine.skills.registry import SOURCE_MARKET, SkillRegistry, SkillView

logger = logging.getLogger(__name__)

ALL_CATEGORY = "全部"


class SkillCatalogError(Exception):
    """Raised when a catalog operation cannot be completed."""


@dataclass(slots=True)
class CatalogEntry:
    """One installable skill advertised by a provider.

    ``install_name`` is the skill name the loader will key on once installed
    (its SKILL.md frontmatter ``name``). It can differ from both ``id`` and the
    display ``name``, so the catalog uses it to tell whether an entry is already
    installed. Providers that cannot cheaply resolve it leave it empty.
    """

    id: str
    name: str
    description: str
    category: str
    icon: str = ""
    downloads: int = 0
    rating: float = 0.0
    source: str = "local"
    install_name: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "downloads": self.downloads,
            "rating": self.rating,
            "source": self.source,
        }


@dataclass(slots=True)
class CatalogView:
    """A :class:`CatalogEntry` plus whether it is already installed locally."""

    entry: CatalogEntry
    installed: bool

    def snapshot(self) -> dict[str, Any]:
        data = self.entry.snapshot()
        data["installed"] = self.installed
        return data


class SkillCatalogProvider(Protocol):
    """Source of installable skill entries (local dir, remote registry, ...)."""

    async def list_entries(self) -> list[CatalogEntry]:
        ...

    async def fetch_skill_files(self, entry_id: str) -> tuple[str, dict[str, bytes]]:
        """Return ``(skill_name, {relative_path: content})`` for an entry."""
        ...


class LocalCatalogProvider:
    """Provider backed by a bundled ``catalog_data`` directory.

    Layout::

        catalog_data/
            catalog.json            # list of entry metadata
            <entry_id>/SKILL.md     # the skill's files, copied on install
            <entry_id>/...

    ``downloads``/``rating`` in ``catalog.json`` are curated static values.
    """

    CATALOG_FILE = "catalog.json"

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or (Path(__file__).parent / "catalog_data")

    async def list_entries(self) -> list[CatalogEntry]:
        return await asyncio.to_thread(self._load_entries_sync)

    def _load_entries_sync(self) -> list[CatalogEntry]:
        catalog_path = self._root / self.CATALOG_FILE
        if not catalog_path.is_file():
            logger.warning("skill_catalog_missing path=%s", catalog_path)
            return []
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("skill_catalog_read_error path=%s", catalog_path)
            return []
        entries: list[CatalogEntry] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                continue
            entry_id = str(item["id"])
            entries.append(
                CatalogEntry(
                    id=entry_id,
                    name=str(item["name"]),
                    description=str(item.get("description", "")),
                    category=str(item.get("category", "")) or ALL_CATEGORY,
                    icon=str(item.get("icon", "")),
                    downloads=int(item.get("downloads", 0) or 0),
                    rating=float(item.get("rating", 0) or 0),
                    source=str(item.get("source", "local")),
                    install_name=self._resolve_install_name(entry_id),
                )
            )
        return entries

    def _resolve_install_name(self, entry_id: str) -> str:
        """Read the entry's SKILL.md frontmatter ``name`` (loader's identity)."""
        skill_file = self._root / entry_id / SkillLoader.SKILL_FILE
        if not skill_file.is_file():
            return ""
        try:
            frontmatter, _ = SkillLoader._split_frontmatter(
                skill_file.read_text(encoding="utf-8")
            )
        except OSError:
            return ""
        if frontmatter and frontmatter.get("name"):
            return str(frontmatter["name"]).strip()
        return ""

    async def fetch_skill_files(self, entry_id: str) -> tuple[str, dict[str, bytes]]:
        return await asyncio.to_thread(self._fetch_sync, entry_id)

    def _fetch_sync(self, entry_id: str) -> tuple[str, dict[str, bytes]]:
        skill_dir = self._root / entry_id
        skill_file = skill_dir / SkillLoader.SKILL_FILE
        if not skill_file.is_file():
            raise SkillCatalogError(f"catalog entry has no SKILL.md: {entry_id}")
        files: dict[str, bytes] = {}
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(skill_dir).as_posix()
                files[rel] = path.read_bytes()
        # Derive the install name from the SKILL.md frontmatter, falling back to
        # the entry id — mirrors how zip import names skills.
        frontmatter, _ = SkillLoader._split_frontmatter(
            skill_file.read_text(encoding="utf-8")
        )
        raw_name = ""
        if frontmatter:
            raw_name = str(frontmatter.get("name") or "")
        name = SkillLoader._normalize_skill_name(raw_name or entry_id)
        return name, files


class SkillCatalog:
    """Browse a provider's entries and install them through the registry."""

    def __init__(
        self,
        *,
        provider: SkillCatalogProvider,
        registry: SkillRegistry,
    ) -> None:
        self._provider = provider
        self._registry = registry

    async def list(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
    ) -> List[CatalogView]:
        entries = await self._provider.list_entries()
        installed_names = {view.name for view in await self._registry.list()}
        wanted_category = (category or "").strip()
        wanted_query = (query or "").strip().lower()
        views: List[CatalogView] = []
        for entry in entries:
            if wanted_category and wanted_category != ALL_CATEGORY and entry.category != wanted_category:
                continue
            if wanted_query and wanted_query not in (entry.name + " " + entry.description).lower():
                continue
            # An entry is installed when the skill name it resolves to (its
            # frontmatter ``name``) is present on disk. Fall back to id/name for
            # providers that don't populate ``install_name``.
            candidates = {entry.install_name, entry.id, entry.name} - {""}
            installed = bool(candidates & installed_names)
            views.append(CatalogView(entry=entry, installed=installed))
        return views

    async def categories(self) -> List[str]:
        entries = await self._provider.list_entries()
        seen: List[str] = []
        for entry in entries:
            if entry.category and entry.category not in seen:
                seen.append(entry.category)
        return [ALL_CATEGORY, *seen]

    async def install(self, entry_id: str) -> SkillView:
        """Install a catalog entry into ``.agent/skills`` via the registry."""
        name, files = await self._provider.fetch_skill_files(entry_id)
        return await self._registry.install_files(
            name,
            files,
            source=SOURCE_MARKET,
            origin=f"market:{entry_id}",
        )


__all__ = [
    "ALL_CATEGORY",
    "CatalogEntry",
    "CatalogView",
    "LocalCatalogProvider",
    "SkillCatalog",
    "SkillCatalogError",
    "SkillCatalogProvider",
]
