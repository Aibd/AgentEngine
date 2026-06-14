"""Tests for SkillMaterializer."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.skills.loader import Skill
from agentengine.skills.materializer import SkillMaterializer


def _make_skill(tmp_path: Path, name: str) -> Skill:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: D\n---\nBody", encoding="utf-8"
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('ok')", encoding="utf-8")
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "ref.md").write_text("# Ref", encoding="utf-8")
    return Skill(
        name=name,
        description="D",
        path=skill_dir / "SKILL.md",
        body="Body",
        base_dir=skill_dir,
    )


def test_materialize_copies_skill_tree(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path / "src", "demo")
    materializer = SkillMaterializer(tmp_path / "workspace")

    result = materializer.materialize(skill)

    assert result.skill_name == "demo"
    assert result.workspace_path == ".skills/demo"
    assert (result.host_path / "SKILL.md").is_file()
    assert (result.host_path / "scripts" / "run.py").is_file()
    assert (result.host_path / "references" / "ref.md").is_file()


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    skill = _make_skill(tmp_path / "src", "demo")
    materializer = SkillMaterializer(tmp_path / "workspace")

    result1 = materializer.materialize(skill)
    result2 = materializer.materialize(skill)

    assert result1.host_path == result2.host_path


def test_materialize_refreshes_on_change(tmp_path: Path) -> None:
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: demo\ndescription: D\n---\nBody", encoding="utf-8"
    )
    (src / "scripts").mkdir(parents=True)
    (src / "scripts" / "run.py").write_text("print('v1')", encoding="utf-8")
    skill = Skill(name="demo", description="D", path=src / "SKILL.md", body="Body", base_dir=src)

    materializer = SkillMaterializer(tmp_path / "workspace")
    materializer.materialize(skill)

    (src / "scripts" / "run.py").write_text("print('v2')", encoding="utf-8")
    result = materializer.materialize(skill)

    assert (result.host_path / "scripts" / "run.py").read_text(encoding="utf-8") == "print('v2')"


def test_materialize_rejects_path_escape(tmp_path: Path) -> None:
    skill_dir = tmp_path / "src" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ../../escape\ndescription: D\n---\nBody", encoding="utf-8"
    )
    skill = Skill(
        name="../../escape",
        description="D",
        path=skill_dir / "SKILL.md",
        body="Body",
        base_dir=skill_dir,
    )
    materializer = SkillMaterializer(tmp_path / "workspace")

    with pytest.raises(ValueError, match="escapes workspace"):
        materializer.materialize(skill)


def test_materialize_skips_symlinks(tmp_path: Path) -> None:
    skill_dir = tmp_path / "src" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: D\n---\nBody", encoding="utf-8"
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run.py").write_text("print('ok')", encoding="utf-8")

    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(parents=True)
    (outside_dir / "leak.txt").write_text("leak", encoding="utf-8")

    assets_dir = skill_dir / "assets"
    assets_dir.mkdir(parents=True)
    try:
        (scripts_dir / "link.txt").symlink_to(secret)
        (assets_dir / "outside").symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported on this platform")

    skill = Skill(
        name="demo",
        description="D",
        path=skill_dir / "SKILL.md",
        body="Body",
        base_dir=skill_dir,
    )
    materializer = SkillMaterializer(tmp_path / "workspace")
    result = materializer.materialize(skill)

    assert (result.host_path / "scripts" / "run.py").is_file()
    assert not (result.host_path / "scripts" / "link.txt").exists()
    assert not (result.host_path / "assets" / "outside").exists()
    assert not (result.host_path / "assets" / "outside" / "leak.txt").exists()
