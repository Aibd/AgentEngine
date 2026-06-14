"""Tests for the ReadSkillResource tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin.skill_resource_tool import ReadSkillResource


def _write_skill(root: Path, dirname: str, content: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8", newline="")
    return skill_dir


async def test_reads_text_reference(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = _write_skill(
        root, "demo", "---\nname: demo\ndescription: D\n---\nBody"
    )
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "format.md").write_text("# Format", encoding="utf-8")

    tool = ReadSkillResource(SkillLoader(roots=[root]))
    result = await tool.run(skill="demo", path="references/format.md")

    assert result == "# Format"


async def test_rejects_undeclared_resource(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "demo", "---\nname: demo\ndescription: D\n---\nBody")

    tool = ReadSkillResource(SkillLoader(roots=[root]))
    result = await tool.run(skill="demo", path="references/undeclared.md")

    assert "not a declared resource" in result


async def test_rejects_path_with_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "demo", "---\nname: demo\ndescription: D\n---\nBody")

    tool = ReadSkillResource(SkillLoader(roots=[root]))
    result = await tool.run(skill="demo", path="../SKILL.md")

    assert "not allowed" in result or "escapes" in result


async def test_rejects_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "demo", "---\nname: demo\ndescription: D\n---\nBody")

    tool = ReadSkillResource(SkillLoader(roots=[root]))
    result = await tool.run(skill="demo", path="/etc/passwd")

    assert "absolute" in result.lower()


async def test_rejects_disabled_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "demo", "---\nname: demo\ndescription: D\n---\nBody")

    tool = ReadSkillResource(SkillLoader(roots=[root]), enabled_names=set())
    result = await tool.run(skill="demo", path="references/format.md")

    assert "disabled" in result.lower()


async def test_binary_resource_returns_metadata(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = _write_skill(
        root, "demo", "---\nname: demo\ndescription: D\n---\nBody"
    )
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "assets" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    tool = ReadSkillResource(SkillLoader(roots=[root]))
    result = await tool.run(skill="demo", path="assets/diagram.png")

    assert "Binary resource" in result
    assert "image/png" in result or "octet-stream" in result


async def test_large_text_resource_is_truncated(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = _write_skill(
        root, "demo", "---\nname: demo\ndescription: D\n---\nBody"
    )
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "huge.md").write_text("x" * 200, encoding="utf-8")

    tool = ReadSkillResource(
        SkillLoader(roots=[root]), text_max_chars=50
    )
    result = await tool.run(skill="demo", path="references/huge.md")

    assert result.startswith("x" * 50)
    assert "truncated" in result.lower()


async def test_unknown_skill(tmp_path: Path) -> None:
    tool = ReadSkillResource(SkillLoader(roots=[tmp_path / "empty"]))
    result = await tool.run(skill="missing", path="references/x.md")

    assert "Unknown skill" in result
