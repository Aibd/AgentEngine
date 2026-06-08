"""Tests for the skill marketplace catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentengine.skills.catalog import (
    ALL_CATEGORY,
    LocalCatalogProvider,
    SkillCatalog,
    SkillCatalogError,
)
from agentengine.skills.registry import SkillRegistry


def _build_catalog_data(root: Path) -> None:
    """Write a tiny two-entry catalog under *root*."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "catalog.json").write_text(
        json.dumps(
            [
                {
                    "id": "summarize",
                    "name": "Summarize",
                    "description": "Summarize long text",
                    "category": "效率提升",
                    "icon": "S",
                    "downloads": 1000,
                    "rating": 4.5,
                },
                {
                    "id": "data-analysis",
                    "name": "数据分析",
                    "description": "Analyze tables",
                    "category": "数据分析",
                    "icon": "D",
                    "downloads": 500,
                    "rating": 4.2,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sm = root / "summarize"
    sm.mkdir()
    (sm / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize long text\n---\nSummarize: ${ARGUMENTS}",
        encoding="utf-8",
    )
    # Frontmatter name (underscore) differs from the entry id (hyphen): exercises
    # the install-name resolution + installed-detection path.
    da = root / "data-analysis"
    da.mkdir()
    (da / "SKILL.md").write_text(
        "---\nname: data_analysis\ndescription: Analyze tables\n---\nAnalyze.",
        encoding="utf-8",
    )
    (da / "helper.md").write_text("reference", encoding="utf-8")


def _catalog(tmp_path: Path) -> SkillCatalog:
    (tmp_path / ".agent" / "skills").mkdir(parents=True)
    _build_catalog_data(tmp_path / "catalog_data")
    registry = SkillRegistry(cwd=tmp_path, db_path=tmp_path / "meta.db")
    provider = LocalCatalogProvider(root=tmp_path / "catalog_data")
    return SkillCatalog(provider=provider, registry=registry)


async def test_list_returns_entries_not_installed(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    views = await catalog.list()
    assert {v.entry.id for v in views} == {"summarize", "data-analysis"}
    assert all(v.installed is False for v in views)


async def test_categories_aggregates_with_all_first(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    cats = await catalog.categories()
    assert cats[0] == ALL_CATEGORY
    assert set(cats) == {ALL_CATEGORY, "效率提升", "数据分析"}


async def test_category_filter(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    views = await catalog.list(category="数据分析")
    assert {v.entry.id for v in views} == {"data-analysis"}


async def test_query_filter(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    views = await catalog.list(query="summarize")
    assert {v.entry.id for v in views} == {"summarize"}


async def test_install_lands_in_registry_and_marks_installed(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    view = await catalog.install("summarize")
    assert view.name == "summarize"
    assert view.source == "market"
    assert view.enabled is True
    assert (tmp_path / ".agent" / "skills" / "summarize" / "SKILL.md").is_file()

    refreshed = await catalog.list()
    summarize = next(v for v in refreshed if v.entry.id == "summarize")
    assert summarize.installed is True


async def test_install_resolves_frontmatter_name_mismatch(tmp_path: Path) -> None:
    """Entry id ``data-analysis`` installs as frontmatter name ``data_analysis``."""
    catalog = _catalog(tmp_path)
    view = await catalog.install("data-analysis")
    assert view.name == "data_analysis"
    # Extra files in the entry directory come along.
    skill_dir = tmp_path / ".agent" / "skills" / "data-analysis"
    assert (skill_dir / "helper.md").is_file()

    refreshed = await catalog.list()
    da = next(v for v in refreshed if v.entry.id == "data-analysis")
    assert da.installed is True


async def test_install_twice_raises_file_exists(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    await catalog.install("summarize")
    with pytest.raises(FileExistsError):
        await catalog.install("summarize")


async def test_install_unknown_entry_raises(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    with pytest.raises(SkillCatalogError):
        await catalog.install("does-not-exist")


async def test_installed_market_skill_is_deletable(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    registry = catalog._registry  # type: ignore[attr-defined]
    view = await catalog.install("summarize")
    await registry.delete(view.name)  # market source must be deletable
    assert all(v.name != "summarize" for v in await registry.list())


async def test_bundled_catalog_loads(tmp_path: Path) -> None:
    """The real shipped catalog_data parses and every entry installs cleanly."""
    (tmp_path / ".agent" / "skills").mkdir(parents=True)
    registry = SkillRegistry(cwd=tmp_path, db_path=tmp_path / "meta.db")
    catalog = SkillCatalog(provider=LocalCatalogProvider(), registry=registry)

    entries = await catalog.list()
    assert len(entries) >= 6  # curated starter set

    for view in entries:
        installed = await catalog.install(view.entry.id)
        assert installed.source == "market"
