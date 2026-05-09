from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.tools.builtin.glob_tool import GlobTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "src" / "c.txt").write_text("c", encoding="utf-8")
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "d.py").write_text("d", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.py").write_text("nope", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("nope", encoding="utf-8")
    return tmp_path


class TestGlobTool:
    async def test_recursive_pattern(self, workspace: Path) -> None:
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="**/*.py")
        assert "src/a.py" in out.replace("\\", "/")
        assert "src/b.py" in out.replace("\\", "/")
        assert "src/deep/d.py" in out.replace("\\", "/")

    async def test_single_segment_pattern_walks_recursively(self, workspace: Path) -> None:
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="*.py")
        # rglob on bare "*.py" should still descend.
        assert "a.py" in out
        assert "d.py" in out

    async def test_path_argument_scopes_search(self, workspace: Path) -> None:
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="**/*.py", path="src/deep")
        assert "d.py" in out
        assert "a.py" not in out

    async def test_ignores_node_modules_and_git(self, workspace: Path) -> None:
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="**/*")
        assert "node_modules" not in out
        assert ".git" not in out

    async def test_no_matches_returns_message(self, workspace: Path) -> None:
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="**/*.rs")
        assert "No files matched" in out

    async def test_empty_pattern_errors(self, workspace: Path) -> None:
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="")
        assert out.startswith("Error")

    async def test_path_outside_workspace_errors(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path.parent
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="**/*.py", path=str(outside))
        assert "escapes workspace" in out

    async def test_limit_truncates_results(self, workspace: Path) -> None:
        for i in range(20):
            (workspace / "src" / f"x{i}.py").write_text(str(i), encoding="utf-8")
        tool = GlobTool(workspace_root=workspace)
        out = await tool.run(pattern="**/*.py", limit=5)
        assert "truncated to 5" in out
