"""Tests for RunSkillScript tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin.skill_script_tool import RunSkillScript


def _write_skill(root: Path, dirname: str, content: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8", newline="")
    return skill_dir


def _make_tool(tmp_path: Path, skill_name: str, enabled: set[str] | None = None) -> tuple[RunSkillScript, MagicMock]:
    root = tmp_path / "skills"
    skill_dir = _write_skill(
        root,
        skill_name,
        f"---\nname: {skill_name}\ndescription: D\n---\nBody",
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('ok')", encoding="utf-8")

    sandbox = MagicMock()
    sandbox.exec_argv.return_value = {
        "exit_code": 0,
        "stdout": "ok\n",
        "stderr": "",
        "timed_out": False,
        "elapsed_seconds": 0.1,
    }
    manager = MagicMock()
    manager.acquire.return_value = sandbox

    tool = RunSkillScript(
        loader=SkillLoader(roots=[root]),
        sandbox_manager=manager,
        conversation_id="conv-1",
        enabled_names=enabled,
        workspace_root=tmp_path / "workspace",
    )
    return tool, sandbox


async def test_runs_declared_script(tmp_path: Path) -> None:
    tool, sandbox = _make_tool(tmp_path, "demo")

    result = await tool.run(skill="demo", script="scripts/run.py")

    assert "exit=0" in result
    assert "ok" in result
    sandbox.exec_argv.assert_called_once()
    argv = sandbox.exec_argv.call_args[0][0]
    assert argv[0] == "python"
    assert argv[1] == "scripts/run.py"


async def test_passes_args_as_array(tmp_path: Path) -> None:
    tool, sandbox = _make_tool(tmp_path, "demo")

    await tool.run(skill="demo", script="scripts/run.py", args=["a", "b c"])

    argv = sandbox.exec_argv.call_args[0][0]
    assert argv[2:] == ["a", "b c"]


async def test_rejects_undeclared_script(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path, "demo")

    result = await tool.run(skill="demo", script="scripts/other.py")

    assert "not a declared" in result or "not a declared script" in result
    assert "scripts/run.py" in result


async def test_rejects_disabled_skill(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path, "demo", enabled=set())

    result = await tool.run(skill="demo", script="scripts/run.py")

    assert "disabled" in result.lower()


async def test_rejects_path_escape(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path, "demo")

    result = await tool.run(skill="demo", script="../SKILL.md")

    assert "Invalid script path" in result or "not allowed" in result or "not a declared" in result


async def test_unknown_skill(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path, "demo")

    result = await tool.run(skill="missing", script="scripts/run.py")

    assert "Unknown skill" in result


async def test_host_exec_works_without_sandbox(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = _write_skill(
        root,
        "web-search",
        "---\nname: web-search\ndescription: D\nhost_exec: true\n---\nBody",
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "search.py").write_text(
        "import sys\nprint('found', sys.argv[1] if len(sys.argv) > 1 else '')\n",
        encoding="utf-8",
    )

    tool = RunSkillScript(
        loader=SkillLoader(roots=[root]),
        conversation_id="conv-1",
        sandbox_manager=None,
        workspace_root=tmp_path / "workspace",
    )

    result = await tool.run(
        skill="web-search",
        script="scripts/search.py",
        args=["hello"],
    )

    assert "exit=0" in result
    assert "found hello" in result


async def test_non_host_exec_requires_sandbox(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_dir = _write_skill(
        root,
        "demo",
        "---\nname: demo\ndescription: D\n---\nBody",
    )
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('ok')", encoding="utf-8")

    tool = RunSkillScript(
        loader=SkillLoader(roots=[root]),
        conversation_id="conv-1",
        sandbox_manager=None,
        workspace_root=tmp_path / "workspace",
    )

    result = await tool.run(skill="demo", script="scripts/run.py")

    assert "sandbox" in result.lower()
