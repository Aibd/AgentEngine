from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.tools.builtin.grep_tool import GrepTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "def hello():\n    return 1\n\ndef world():\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "b.py").write_text(
        "import os\n\ndef hello():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Hello\nThis is a test.\n",
        encoding="utf-8",
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.py").write_text("hello", encoding="utf-8")
    return tmp_path


def _force_python_engine(tool: GrepTool) -> GrepTool:
    # Force the Python fallback so tests don't depend on whether ripgrep is
    # installed on the runner.
    tool._rg_path = None
    return tool


class TestGrepToolPythonEngine:
    async def test_files_with_matches(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="def hello")
        assert "src/a.py" in out.replace("\\", "/")
        assert "src/b.py" in out.replace("\\", "/")
        assert "README.md" not in out

    async def test_content_mode_includes_line_numbers(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="def hello", output_mode="content")
        assert "src/a.py:1:" in out.replace("\\", "/")

    async def test_count_mode_reports_per_file(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="def ", output_mode="count")
        assert ":2" in out  # a.py has two def lines

    async def test_case_insensitive(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="hello", case_insensitive=True)
        assert "README.md" in out

    async def test_glob_filter(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="hello", glob="*.md", case_insensitive=True)
        assert "README.md" in out
        assert "src/a.py" not in out.replace("\\", "/")

    async def test_invalid_regex_returns_error(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="(unclosed")
        assert out.startswith("Error: invalid regex")

    async def test_no_matches_message(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="absolutely_no_match_here")
        assert "No matches" in out

    async def test_ignores_node_modules(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="hello")
        assert "node_modules" not in out

    async def test_path_escaping_rejected(self, workspace: Path, tmp_path: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="x", path=str(tmp_path.parent))
        assert "escapes workspace" in out

    async def test_unknown_output_mode(self, workspace: Path) -> None:
        tool = _force_python_engine(GrepTool(workspace_root=workspace))
        out = await tool.run(pattern="hello", output_mode="bogus")
        assert "unknown output_mode" in out
