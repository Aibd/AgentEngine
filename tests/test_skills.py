from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.base.context import AgentContext
from agentkit.skills.loader import Skill, SkillLoader
from agentkit.tools.builtin.skill_tool import SkillTool
from agentkit.tools.collection import ToolCollection


def _write_skill(root: Path, dirname: str, content: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8", newline="")
    return skill_file


def test_loader_discovers_parses_and_caches_skills(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_file = _write_skill(
        root,
        "writer",
        "---\nname: writer\ndescription: Writes things\n---\n\nUse concise prose.",
    )
    loader = SkillLoader(roots=[root])

    skills = loader.discover()

    assert list(skills) == ["writer"]
    assert skills["writer"].description == "Writes things"
    assert skills["writer"].body == "\nUse concise prose."

    skill_file.write_text(
        "---\nname: writer\ndescription: Updated\n---\n\nNew body.",
        encoding="utf-8",
    )
    assert loader.discover()["writer"].description == "Writes things"
    assert loader.discover(force=True)["writer"].description == "Updated"


def test_loader_frontmatter_supports_windows_newlines(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "windows",
        "---\r\nname: windows\r\ndescription: CRLF skill\r\n---\r\nBody",
    )

    skill = SkillLoader(roots=[root]).discover()["windows"]

    assert skill.description == "CRLF skill"
    assert skill.body == "Body"


def test_loader_ignores_unsupported_allowed_tools_frontmatter(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "limited",
        "---\nname: limited\ndescription: Limited\nallowed-tools:\n  - Read\n---\nBody",
    )

    skill = SkillLoader(roots=[root]).discover()["limited"]

    assert not hasattr(skill, "allowed_tools")


def test_loader_scaffolds_new_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    loader = SkillLoader(roots=[root])

    skill_file = loader.scaffold("My_Skill", description="Does useful work")

    assert skill_file == root / "my-skill" / "SKILL.md"
    skill = loader.discover()["my-skill"]
    assert skill.description == "Does useful work"
    assert "${ARGUMENTS}" in skill.body

    with pytest.raises(FileExistsError):
        loader.scaffold("my-skill")


def test_skill_prompt_is_cached(tmp_path: Path) -> None:
    skill = Skill(name="demo", description="", path=tmp_path / "SKILL.md", body="One")

    assert skill.prompt == f"Base directory: {tmp_path}\n\nOne"
    skill.body = "Two"
    assert skill.prompt == f"Base directory: {tmp_path}\n\nOne"


async def test_skill_tool_invokes_skill_with_named_kwargs(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "summarize",
        "---\nname: summarize\ndescription: Summarize text\n---\nSummarize: ${ARGUMENTS}",
    )
    tool = SkillTool(SkillLoader(roots=[root]))

    result = await tool.run(skill="summarize", args="hello")

    assert "[Skill: summarize]" in result
    assert "Description: Summarize text" in result
    assert "Summarize: hello" in result


async def test_skill_tool_reports_unknown_skill(tmp_path: Path) -> None:
    tool = SkillTool(SkillLoader(roots=[tmp_path / "missing"]))

    result = await tool.run(skill="missing")

    assert result == "Unknown skill: missing. Available: (none)"


def test_context_auto_registers_skill_tool_with_injected_loader(tmp_path: Path) -> None:
    loader = SkillLoader(roots=[tmp_path / "custom-skills"])
    collection = ToolCollection()

    context = AgentContext(
        request_id="req-1",
        query="hello",
        tool_collection=collection,
        extras={"skill_loader": loader},
    )

    tool = context.tool_collection.require("Skill")
    assert isinstance(tool, SkillTool)
    assert tool._loader is loader


def test_context_does_not_replace_existing_skill_tool(tmp_path: Path) -> None:
    existing_loader = SkillLoader(roots=[tmp_path / "existing"])
    existing_tool = SkillTool(existing_loader)
    collection = ToolCollection([existing_tool])

    context = AgentContext(
        request_id="req-1",
        query="hello",
        tool_collection=collection,
        extras={"skill_loader": SkillLoader(roots=[tmp_path / "other"])},
    )

    assert context.tool_collection.require("Skill") is existing_tool
