from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.base.context import AgentContext
from agentengine.skills.loader import (
    Skill,
    SkillLoader,
    SkillResourceLimitError,
    SkillResources,
)
from agentengine.tools.builtin.skill_tool import SkillTool
from agentengine.tools.collection import ToolCollection


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
    skill = Skill(
        name="demo",
        description="",
        path=tmp_path / "SKILL.md",
        body="One",
        base_dir=tmp_path,
    )

    assert skill.prompt == f"Base directory: {tmp_path}\n\nOne"
    skill.body = "Two"
    assert skill.prompt == f"Base directory: {tmp_path}\n\nOne"


def test_skill_discovers_bundled_resources(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "data-analysis"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: Analyze data\n---\nAnalyze.",
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "analyze.py").write_text("print('ok')", encoding="utf-8")
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "format.md").write_text("# Format", encoding="utf-8")
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "assets" / "template.csv").write_text("a,b", encoding="utf-8")

    skill = SkillLoader(roots=[root]).discover()["data-analysis"]

    assert skill.base_dir == skill_dir
    assert skill.resources == SkillResources(
        scripts=("scripts/analyze.py",),
        references=("references/format.md",),
        assets=("assets/template.csv",),
    )


def test_skill_ignores_hidden_and_non_resource_files(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "clean"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: clean\ndescription: Clean\n---\nBody", encoding="utf-8"
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.sh").write_text("echo ok", encoding="utf-8")
    (skill_dir / "scripts" / ".hidden.sh").write_text("hidden", encoding="utf-8")
    (skill_dir / "scripts" / "__pycache__").mkdir(parents=True)
    (skill_dir / "scripts" / "__pycache__" / "cached.pyc").write_text(
        "x", encoding="utf-8"
    )
    (skill_dir / "other").mkdir(parents=True)
    (skill_dir / "other" / "file.txt").write_text("ignored", encoding="utf-8")

    skill = SkillLoader(roots=[root]).discover()["clean"]

    assert skill.resources == SkillResources(scripts=("scripts/run.sh",))


def test_skill_resource_paths_are_posix_and_sorted(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "sorted"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sorted\ndescription: Sorted\n---\nBody", encoding="utf-8"
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "z.py").write_text("z", encoding="utf-8")
    (skill_dir / "scripts" / "a.py").write_text("a", encoding="utf-8")
    (skill_dir / "scripts" / "sub").mkdir(parents=True)
    (skill_dir / "scripts" / "sub" / "b.py").write_text("b", encoding="utf-8")

    skill = SkillLoader(roots=[root]).discover()["sorted"]

    assert skill.resources is not None
    assert skill.resources.scripts == (
        "scripts/a.py",
        "scripts/sub/b.py",
        "scripts/z.py",
    )
    assert "/" in skill.resources.scripts[0]


def test_skill_rejects_resource_outside_skill_dir(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "escape"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: escape\ndescription: Escape\n---\nBody", encoding="utf-8"
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "link.txt").symlink_to(outside)

    skill = SkillLoader(roots=[root]).discover()["escape"]

    assert skill.resources == SkillResources()


def test_skill_resource_limit_raises(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "many"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: many\ndescription: Many\n---\nBody", encoding="utf-8"
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    for i in range(5):
        (scripts_dir / f"s{i}.py").write_text("x", encoding="utf-8")

    with pytest.raises(SkillResourceLimitError):
        Skill._list_resources(skill_dir, max_files=2)


def test_loader_skips_skill_exceeding_resource_limit(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "many"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: many\ndescription: Many\n---\nBody", encoding="utf-8"
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    # Exceed the configured resource-file limit so the loader skips the skill.
    for i in range(Skill.MAX_RESOURCE_FILES + 1):
        (scripts_dir / f"s{i}.py").write_text("x", encoding="utf-8")

    loader = SkillLoader(roots=[root])
    assert "many" not in loader.discover()


def test_skill_activation_content_replaces_arguments_and_escapes_xml(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "xml-skill"
    skill_dir.mkdir(parents=True)
    skill = Skill(
        name="xml-skill",
        description='A "useful" skill',
        path=skill_dir / "SKILL.md",
        body="Args: ${ARGUMENTS}. Use <script> & run.",
        base_dir=skill_dir,
    )

    content = skill.activation_content('foo "bar"')

    assert '<skill_content name="xml-skill" description="A &quot;useful&quot; skill">' in content
    assert "Args: foo &quot;bar&quot;. Use &lt;script&gt; &amp; run." in content
    assert "<script>" not in content
    assert "<file>" not in content  # No resources attached


def test_skill_loader_default_roots_include_agents_and_agent(
    tmp_path: Path,
) -> None:
    loader = SkillLoader(cwd=tmp_path)

    roots = loader._roots
    assert any(str(r).endswith(".agents\\skills") or str(r).endswith(".agents/skills") for r in roots)
    assert any(str(r).endswith(".agent\\skills") or str(r).endswith(".agent/skills") for r in roots)


def test_skill_loader_scaffold_default_root_is_agents(tmp_path: Path) -> None:
    loader = SkillLoader(cwd=tmp_path)

    skill_file = loader.scaffold("demo")

    assert ".agents" in str(skill_file)


async def test_skill_tool_invokes_skill_with_named_kwargs(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(
        root,
        "summarize",
        "---\nname: summarize\ndescription: Summarize text\n---\nSummarize: ${ARGUMENTS}",
    )
    tool = SkillTool(SkillLoader(roots=[root]))

    result = await tool.run(skill="summarize", args="hello")

    content = result.content if hasattr(result, "content") else result
    assert '<skill_content name="summarize"' in content
    assert "Summarize: hello" in content
    assert result.metadata.get("skill_activation") is True
    assert result.metadata.get("skill_name") == "summarize"


async def test_skill_tool_reports_unknown_skill(tmp_path: Path) -> None:
    tool = SkillTool(SkillLoader(roots=[tmp_path / "missing"]))

    result = await tool.run(skill="missing")

    assert result == "Unknown skill: missing. Available: (none)"


async def test_skill_tool_does_not_eagerly_load_resource_contents(
    tmp_path: Path,
) -> None:
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

    content = result.content if hasattr(result, "content") else result
    assert "references/huge.md" in content
    assert "x" * 100 not in content
    assert result.metadata.get("skill_activation") is True


def test_context_does_not_auto_register_skill_tool(tmp_path: Path) -> None:
    loader = SkillLoader(roots=[tmp_path / "custom-skills"])
    collection = ToolCollection()

    context = AgentContext(
        request_id="req-1",
        query="hello",
        tool_collection=collection,
        extras={"skill_loader": loader},
    )

    assert context.tool_collection.get("Skill") is None


def test_context_preserves_explicit_skill_tool(tmp_path: Path) -> None:
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
