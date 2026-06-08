"""Tests for the skill lifecycle registry (import / export / enable / delete)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from agentengine.skills.loader import SkillLoader
from agentengine.skills.registry import (
    SOURCE_BUILTIN,
    SOURCE_IMPORTED,
    SkillImportError,
    SkillRegistry,
)
from agentengine.tools.builtin.skill_tool import SkillTool


def _make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _registry(tmp_path: Path) -> SkillRegistry:
    (tmp_path / ".agent" / "skills").mkdir(parents=True)
    return SkillRegistry(cwd=tmp_path, db_path=tmp_path / "meta.db")


def _write_builtin(tmp_path: Path, name: str, description: str = "builtin") -> None:
    skill_dir = tmp_path / ".agent" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nBody",
        encoding="utf-8",
    )


# -- import -----------------------------------------------------------------


async def test_import_nested_skill_md(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    data = _make_zip(
        {"translator/SKILL.md": "---\nname: translator\ndescription: Translate\n---\nBody"}
    )

    view = await reg.import_zip(data, original_filename="translator.zip")

    assert view.name == "translator"
    assert view.source == SOURCE_IMPORTED
    assert view.enabled is True
    assert view.origin == "translator.zip"
    assert (tmp_path / ".agent" / "skills" / "translator" / "SKILL.md").is_file()


async def test_import_top_level_skill_md_uses_frontmatter_name(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    data = _make_zip({"SKILL.md": "---\nname: topskill\ndescription: Top\n---\nBody"})

    view = await reg.import_zip(data)

    assert view.name == "topskill"


async def test_import_deep_repo_layout(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    data = _make_zip(
        {
            "repo-main/skills/summarizer/SKILL.md": "---\nname: summarizer\ndescription: S\n---\nB",
            "repo-main/README.md": "ignored",
            "repo-main/skills/summarizer/helper.py": "print('hi')",
        }
    )

    view = await reg.import_zip(data)

    assert view.name == "summarizer"
    # Files under the skill root are copied; siblings outside it are not.
    skill_dir = tmp_path / ".agent" / "skills" / "summarizer"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "helper.py").is_file()
    assert not (skill_dir / "README.md").exists()


async def test_import_without_skill_md_raises(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    with pytest.raises(SkillImportError):
        await reg.import_zip(_make_zip({"notes.txt": "no skill here"}))


async def test_import_bad_zip_raises(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    with pytest.raises(SkillImportError):
        await reg.import_zip(b"not a zip at all")


async def test_import_rejects_zip_slip(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    # The only SKILL.md uses a parent-escape path; it must be filtered out,
    # leaving no usable member and thus a rejected import.
    data = _make_zip({"../evil/SKILL.md": "---\nname: evil\n---\nx"})
    with pytest.raises(SkillImportError):
        await reg.import_zip(data)
    assert not (tmp_path.parent / "evil").exists()


async def test_duplicate_import_raises_file_exists(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    data = _make_zip({"dup/SKILL.md": "---\nname: dup\ndescription: D\n---\nB"})
    await reg.import_zip(data)
    with pytest.raises(FileExistsError):
        await reg.import_zip(data)


# -- enable / disable -------------------------------------------------------


async def test_builtin_skill_defaults_enabled(tmp_path: Path) -> None:
    _write_builtin(tmp_path, "codebase-research")
    reg = _registry_existing(tmp_path)

    views = await reg.list()
    assert len(views) == 1
    assert views[0].name == "codebase-research"
    assert views[0].enabled is True
    assert views[0].source == SOURCE_BUILTIN
    assert "codebase-research" in await reg.enabled_names()


async def test_set_enabled_toggles_membership(tmp_path: Path) -> None:
    _write_builtin(tmp_path, "alpha")
    reg = _registry_existing(tmp_path)

    await reg.set_enabled("alpha", False)
    assert "alpha" not in await reg.enabled_names()

    await reg.set_enabled("alpha", True)
    assert "alpha" in await reg.enabled_names()


async def test_set_enabled_unknown_raises_keyerror(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    with pytest.raises(KeyError):
        await reg.set_enabled("ghost", False)


async def test_disable_preserves_imported_source(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    await reg.import_zip(_make_zip({"imp/SKILL.md": "---\nname: imp\ndescription: I\n---\nB"}))

    await reg.set_enabled("imp", False)
    view = next(v for v in await reg.list() if v.name == "imp")
    assert view.source == SOURCE_IMPORTED
    assert view.enabled is False


# -- delete -----------------------------------------------------------------


async def test_delete_imported_removes_files_and_meta(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    await reg.import_zip(_make_zip({"gone/SKILL.md": "---\nname: gone\ndescription: G\n---\nB"}))
    assert (tmp_path / ".agent" / "skills" / "gone").is_dir()

    await reg.delete("gone")

    assert not (tmp_path / ".agent" / "skills" / "gone").exists()
    assert all(v.name != "gone" for v in await reg.list())


async def test_delete_builtin_raises_permission_error(tmp_path: Path) -> None:
    _write_builtin(tmp_path, "protected")
    reg = _registry_existing(tmp_path)
    with pytest.raises(PermissionError):
        await reg.delete("protected")
    assert (tmp_path / ".agent" / "skills" / "protected").is_dir()


# -- export -----------------------------------------------------------------


async def test_export_zip_roundtrips(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    await reg.import_zip(
        _make_zip(
            {
                "pack/SKILL.md": "---\nname: pack\ndescription: P\n---\nBody",
                "pack/ref.md": "reference",
            }
        )
    )

    blob = await reg.export_zip("pack")

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = set(archive.namelist())
    assert names == {"pack/SKILL.md", "pack/ref.md"}


async def test_export_unknown_raises_keyerror(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    with pytest.raises(KeyError):
        await reg.export_zip("missing")


# -- get detail -------------------------------------------------------------


async def test_get_returns_body(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    await reg.import_zip(
        _make_zip({"d/SKILL.md": "---\nname: d\ndescription: Dd\n---\nThe body text"})
    )

    detail = await reg.get("d")

    assert detail is not None
    assert detail.body.strip() == "The body text"
    assert detail.snapshot()["body"].strip() == "The body text"


async def test_get_missing_returns_none(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    assert await reg.get("nope") is None


# -- SkillTool enable filter ------------------------------------------------


async def test_skill_tool_blocks_disabled_skill(tmp_path: Path) -> None:
    _write_builtin(tmp_path, "blocked", description="blocked skill")
    loader = SkillLoader(roots=[tmp_path / ".agent" / "skills"])
    tool = SkillTool(loader, enabled_names={"something-else"})

    result = await tool.run(skill="blocked")

    assert "disabled" in result.lower()


async def test_skill_tool_allows_enabled_skill(tmp_path: Path) -> None:
    _write_builtin(tmp_path, "ok", description="ok skill")
    loader = SkillLoader(roots=[tmp_path / ".agent" / "skills"])
    tool = SkillTool(loader, enabled_names={"ok"})

    result = await tool.run(skill="ok")

    assert "[Skill: ok]" in result


async def test_skill_tool_callable_enabled_names(tmp_path: Path) -> None:
    _write_builtin(tmp_path, "dyn")
    loader = SkillLoader(roots=[tmp_path / ".agent" / "skills"])
    state = {"dyn"}
    tool = SkillTool(loader, enabled_names=lambda: state)

    assert "[Skill: dyn]" in await tool.run(skill="dyn")
    state.clear()
    assert "disabled" in (await tool.run(skill="dyn")).lower()


def _registry_existing(tmp_path: Path) -> SkillRegistry:
    """Registry whose skills dir already exists (builtin written by the test)."""
    return SkillRegistry(cwd=tmp_path, db_path=tmp_path / "meta.db")
