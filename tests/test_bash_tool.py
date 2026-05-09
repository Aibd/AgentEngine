from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentkit.tools.builtin.bash_tool import BashTool, is_destructive_command


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _shell_for_platform() -> str:
    return "cmd" if sys.platform == "win32" else "sh"


class TestBashTool:
    async def test_runs_simple_command(self, workspace: Path) -> None:
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        if sys.platform == "win32":
            out = await tool.run(command="echo hello")
        else:
            out = await tool.run(command="echo hello")
        assert "exit=0" in out
        assert "hello" in out

    async def test_captures_nonzero_exit(self, workspace: Path) -> None:
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        if sys.platform == "win32":
            out = await tool.run(command="exit 7")
        else:
            out = await tool.run(command="exit 7")
        assert "exit=7" in out

    async def test_captures_stderr(self, workspace: Path) -> None:
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        if sys.platform == "win32":
            out = await tool.run(command="echo oops 1>&2 && exit 1")
        else:
            out = await tool.run(command="echo oops 1>&2; exit 1")
        assert "stderr" in out
        assert "oops" in out
        assert "exit=1" in out

    async def test_runs_in_workspace_root(self, workspace: Path) -> None:
        marker = workspace / "marker.txt"
        marker.write_text("here", encoding="utf-8")
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        if sys.platform == "win32":
            out = await tool.run(command="dir /b")
        else:
            out = await tool.run(command="ls")
        assert "marker.txt" in out

    async def test_timeout_kills_long_command(self, workspace: Path) -> None:
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        if sys.platform == "win32":
            # ping -n 6 sleeps about 5s on Windows
            out = await tool.run(command="ping -n 6 127.0.0.1 >nul", timeout=1)
        else:
            out = await tool.run(command="sleep 5", timeout=1)
        assert "timed out" in out

    async def test_empty_command_returns_error(self, workspace: Path) -> None:
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        out = await tool.run(command="   ")
        assert out.startswith("Error")

    async def test_destructive_command_adds_banner(self, workspace: Path) -> None:
        # Use a harmless rm of a non-existent file — we just want to see the
        # banner triggered by the heuristic.
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        if sys.platform == "win32":
            out = await tool.run(command="del /Q nonexistent.txt")
        else:
            out = await tool.run(command="rm -f nonexistent.txt")
        assert "destructive" in out.lower()

    async def test_invalid_workspace_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            BashTool(workspace_root=tmp_path / "missing")

    def test_to_openai_tool_shape(self, workspace: Path) -> None:
        tool = BashTool(workspace_root=workspace, shell=_shell_for_platform())
        schema = tool.to_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "bash"
        assert "command" in schema["function"]["parameters"]["properties"]


class TestDestructiveHeuristic:
    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /tmp/foo",
            "  rm file",
            "git push --force origin main",
            "git push -f",
            "git reset --hard HEAD~1",
            "git clean -fd",
            "git branch -D feature",
            "del C:\\Users\\foo.txt",
            "Remove-Item bar",
            "shutdown /s",
        ],
    )
    def test_detects_destructive(self, command: str) -> None:
        assert is_destructive_command(command)

    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "git status",
            "git log --oneline",
            "echo hello",
            "python -m pytest",
        ],
    )
    def test_passes_safe_commands(self, command: str) -> None:
        assert not is_destructive_command(command)
