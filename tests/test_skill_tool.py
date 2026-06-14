"""Tests for the SkillTool activation tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin.skill_tool import SkillTool


def _write_skill(root: Path, dirname: str, content: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8", newline="")
    return skill_file


async def test_run_returns_structured_activation(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "summarize",
        "---\nname: summarize\ndescription: Summarize text\n---\nSummarize: ${ARGUMENTS}",
    )
    tool = SkillTool(SkillLoader(roots=[root]))

    result = await tool.run(skill="summarize", args="hello")

    assert '<skill_content name="summarize"' in result.content
    assert "Summarize: hello" in result.content
    assert "<skill_directory>" in result.content
    assert "<skill_resources>" in result.content
    assert result.metadata.get("skill_activation") is True


async def test_run_replaces_arguments(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "echo",
        "---\nname: echo\ndescription: Echo\n---\nEcho back: ${ARGUMENTS}",
    )
    tool = SkillTool(SkillLoader(roots=[root]))

    result = await tool.run(skill="echo", args='foo "bar"')

    assert 'foo &quot;bar&quot;' in result.content


async def test_run_reports_unknown_skill(tmp_path: Path) -> None:
    tool = SkillTool(SkillLoader(roots=[tmp_path / "missing"]))

    result = await tool.run(skill="missing")

    assert result == "Unknown skill: missing. Available: (none)"


async def test_run_blocks_disabled_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "blocked", "---\nname: blocked\ndescription: B\n---\nBody")
    tool = SkillTool(SkillLoader(roots=[root]), enabled_names=set())

    result = await tool.run(skill="blocked")

    content = result.content if hasattr(result, "content") else result
    assert "disabled" in content.lower()


def test_to_openai_tool_includes_enabled_skills_in_enum(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "alpha", "---\nname: alpha\ndescription: A\n---\nBody")
    _write_skill(root, "beta", "---\nname: beta\ndescription: B\n---\nBody")
    tool = SkillTool(SkillLoader(roots=[root]), enabled_names={"alpha"})

    definition = tool.to_openai_tool()

    params = definition["function"]["parameters"]
    assert params["properties"]["skill"]["enum"] == ["alpha"]
    assert "beta" not in definition["function"]["description"]
    assert "alpha" in definition["function"]["description"]


def test_to_openai_tool_without_enabled_filter_lists_all_skills(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "alpha", "---\nname: alpha\ndescription: A\n---\nBody")
    _write_skill(root, "beta", "---\nname: beta\ndescription: B\n---\nBody")
    tool = SkillTool(SkillLoader(roots=[root]))

    definition = tool.to_openai_tool()

    params = definition["function"]["parameters"]
    assert set(params["properties"]["skill"]["enum"]) == {"alpha", "beta"}


def test_to_openai_tool_with_no_skills_has_empty_description(tmp_path: Path) -> None:
    tool = SkillTool(SkillLoader(roots=[tmp_path / "empty"]))

    definition = tool.to_openai_tool()

    assert "No skills are currently enabled" in definition["function"]["description"]
    assert "enum" not in definition["function"]["parameters"].get("properties", {}).get("skill", {})


async def test_run_does_not_eagerly_load_resource_contents(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "lazy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: lazy\ndescription: Lazy\n---\nBody", encoding="utf-8"
    )
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "huge.md").write_text("x" * 100_000, encoding="utf-8")

    tool = SkillTool(SkillLoader(roots=[root]))
    result = await tool.run(skill="lazy")

    assert "references/huge.md" in result.content
    assert "x" * 100 not in result.content
