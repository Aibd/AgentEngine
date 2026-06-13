"""Tests for the Markdown + frontmatter definition loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine import AgentDefinition, load_preset, load_presets, load_definition, load_definitions
from agentengine.base.context import AgentContext


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_body_becomes_instructions(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "chat.md",
        "---\nname: chat\ndescription: A chat agent.\n---\n"
        "You are a helpful assistant.\n",
    )
    definition = load_definition(md)
    assert isinstance(definition, AgentDefinition)
    assert definition.name == "chat"
    assert definition.description == "A chat agent."
    assert definition.instructions == "You are a helpful assistant."
    assert definition.setup is None


def test_explicit_instructions_override_body(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "x.md",
        "---\nname: x\ninstructions: From frontmatter.\n---\nIgnored body.\n",
    )
    assert load_definition(md).instructions == "From frontmatter."


def test_name_defaults_to_file_stem(tmp_path: Path) -> None:
    md = _write(tmp_path / "researcher.md", "---\ndescription: d\n---\nbody\n")
    assert load_definition(md).name == "researcher"


def test_scalar_fields_are_forwarded(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "x.md",
        "---\nname: x\nmax_messages: 12\nauto_compact_tokens: 2000\n"
        "compaction_keep_recent: 4\n---\nbody\n",
    )
    definition = load_definition(md)
    assert definition.max_messages == 12
    assert definition.auto_compact_tokens == 2000
    assert definition.compaction_keep_recent == 4


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    md = _write(tmp_path / "x.md", "No frontmatter here.\n")
    with pytest.raises(ValueError, match="no YAML frontmatter"):
        load_definition(md)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    md = _write(tmp_path / "x.md", "---\nname: [unclosed\n---\nbody\n")
    with pytest.raises(ValueError, match="Invalid YAML"):
        load_definition(md)


@pytest.mark.parametrize(
    "tools_line",
    ["tools: [read_file, Skill]", "tools: read_file, Skill"],
)
def test_tools_compile_to_setup_hook(tmp_path: Path, tools_line: str) -> None:
    md = _write(
        tmp_path / "r.md",
        f"---\nname: r\n{tools_line}\n---\nResearch carefully.\n",
    )
    definition = load_definition(md)
    assert definition.extras["tools"] == ("read_file", "Skill")
    assert definition.setup is not None


async def test_setup_hook_attaches_tools_by_name(tmp_path: Path) -> None:
    md = _write(
        tmp_path / "r.md",
        "---\nname: r\ntools: [read_file, Skill]\n---\nbody\n",
    )
    definition = load_definition(md)
    context = AgentContext(request_id="t", query="q")

    assert definition.setup is not None
    await definition.setup(context)

    names = set(context.tool_collection.tool_map)
    assert {"read_file", "Skill"} <= names
    assert type(context.tool_collection.get("read_file")).__name__ == "ReadFileTool"
    assert type(context.tool_collection.get("Skill")).__name__ == "SkillTool"


async def test_setup_hook_is_idempotent(tmp_path: Path) -> None:
    md = _write(tmp_path / "r.md", "---\nname: r\ntools: [read_file]\n---\nbody\n")
    definition = load_definition(md)
    context = AgentContext(request_id="t", query="q")

    assert definition.setup is not None
    await definition.setup(context)
    first = context.tool_collection.get("read_file")
    await definition.setup(context)
    # Same instance — second run must not replace or duplicate.
    assert context.tool_collection.get("read_file") is first


def test_load_definitions_scans_directory(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\nname: a\n---\nA body\n")
    _write(tmp_path / "b.md", "---\nname: b\n---\nB body\n")
    _write(tmp_path / "ignore.txt", "not a definition")
    _write(tmp_path / ".hidden.md", "---\nname: hidden\n---\nx\n")

    definitions = load_definitions(tmp_path)

    assert set(definitions) == {"a", "b"}
    assert definitions["a"].instructions == "A body"


def test_load_definitions_skips_malformed_files(tmp_path: Path) -> None:
    _write(tmp_path / "good.md", "---\nname: good\n---\nok\n")
    _write(tmp_path / "bad.md", "no frontmatter\n")

    definitions = load_definitions(tmp_path)

    assert set(definitions) == {"good"}


def test_load_definitions_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_definitions(tmp_path / "does-not-exist") == {}


# Backward compatibility: old function names still work


def test_backward_compat_load_preset(tmp_path: Path) -> None:
    md = _write(tmp_path / "x.md", "---\nname: x\n---\nX body\n")
    definition = load_preset(md)
    assert isinstance(definition, AgentDefinition)
    assert definition.name == "x"


def test_backward_compat_load_presets(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\nname: a\n---\nA\n")
    _write(tmp_path / "b.md", "---\nname: b\n---\nB\n")
    result = load_presets(tmp_path)
    assert set(result) == {"a", "b"}
    assert isinstance(result["a"], AgentDefinition)
