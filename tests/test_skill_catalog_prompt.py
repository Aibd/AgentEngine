"""Tests for the enabled-skill catalog prompt."""

from __future__ import annotations

from pathlib import Path

from agentengine.skills.catalog_prompt import SkillCatalogPrompt
from agentengine.skills.loader import Skill


def _skill(name: str, description: str, base_dir: Path | None = None) -> Skill:
    return Skill(
        name=name,
        description=description,
        path=(base_dir or Path("/tmp")) / "SKILL.md",
        body="",
        base_dir=base_dir or Path("/tmp"),
    )


def test_render_returns_empty_when_no_skills() -> None:
    assert SkillCatalogPrompt.render([]) == ""


def test_render_includes_name_and_description(tmp_path: Path) -> None:
    skills = [
        _skill("data-analysis", "Analyze structured files.", tmp_path / "a"),
        _skill("summarize", "Summarize long text.", tmp_path / "b"),
    ]

    catalog = SkillCatalogPrompt.render(skills)

    assert "<available_skills>" in catalog
    assert '<name>data-analysis</name>' in catalog
    assert "<description>Analyze structured files.</description>" in catalog
    assert '<name>summarize</name>' in catalog


def test_render_sorts_by_name(tmp_path: Path) -> None:
    skills = [
        _skill("zebra", "Z", tmp_path / "z"),
        _skill("alpha", "A", tmp_path / "a"),
    ]

    catalog = SkillCatalogPrompt.render(skills)

    alpha_pos = catalog.index("alpha")
    zebra_pos = catalog.index("zebra")
    assert alpha_pos < zebra_pos


def test_render_escapes_xml_special_characters(tmp_path: Path) -> None:
    skill = _skill('weird"name', "Use <script> & run.", tmp_path / "x")

    catalog = SkillCatalogPrompt.render([skill])

    assert '&quot;name' in catalog
    assert "&lt;script&gt;" in catalog
    assert "&amp;" in catalog
    assert '<script>' not in catalog


def test_render_includes_usage_instruction(tmp_path: Path) -> None:
    skill = _skill("demo", "Demo skill.", tmp_path / "d")

    catalog = SkillCatalogPrompt.render([skill])

    assert "activate it by calling the Skill tool" in catalog
