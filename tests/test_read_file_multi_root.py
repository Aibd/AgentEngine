"""Tests for ReadFileTool multi-root and sandbox path support."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.tools.builtin.read_file_tool import ReadFileTool


@pytest.fixture
def ws1(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    return tmp_path


@pytest.fixture
def ws2(tmp_path: Path) -> Path:
    ws = tmp_path / "sandbox_sessions" / "conv_001"
    ws.mkdir(parents=True)
    (ws / "output.txt").write_text("sandbox output", encoding="utf-8")
    return ws


class TestMultiRoot:
    async def test_relative_path_searches_all_roots(
        self, ws1: Path, ws2: Path
    ) -> None:
        tool = ReadFileTool(workspace_root=ws1, extra_roots=[str(ws2)])
        out = await tool.run(path="output.txt")
        assert "sandbox output" in out

    async def test_relative_path_falls_back_to_primary(
        self, ws1: Path, ws2: Path
    ) -> None:
        tool = ReadFileTool(workspace_root=ws1, extra_roots=[str(ws2)])
        out = await tool.run(path="a.txt")
        assert "alpha" in out

    async def test_absolute_path_in_extra_root(
        self, ws1: Path, ws2: Path
    ) -> None:
        tool = ReadFileTool(workspace_root=ws1, extra_roots=[str(ws2)])
        abs_path = str(ws2 / "output.txt")
        out = await tool.run(path=abs_path)
        assert "sandbox output" in out

    async def test_absolute_path_outside_all_roots(
        self, ws1: Path, ws2: Path
    ) -> None:
        tool = ReadFileTool(workspace_root=ws1, extra_roots=[str(ws2)])
        out = await tool.run(path="/nonexistent/path.txt")
        assert "Error" in out


class TestTraversalVsMissing:
    async def test_traversal_blocked_even_if_file_absent(
        self, ws1: Path
    ) -> None:
        tool = ReadFileTool(workspace_root=ws1)
        out = await tool.run(path="../etc/passwd")
        assert "escapes workspace" in out.lower()

    async def test_missing_file_shows_not_found(
        self, ws1: Path
    ) -> None:
        tool = ReadFileTool(workspace_root=ws1)
        out = await tool.run(path="missing.txt")
        assert "not found" in out.lower()


class TestNoRoots:
    """When no roots are configured, any existing absolute path is accepted
    (OS enforces read permissions for this read-only tool)."""

    async def test_absolute_path_accepted_without_roots(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "free.txt"
        f.write_text("free", encoding="utf-8")
        tool = ReadFileTool(workspace_root=None, extra_roots=[])
        tool._roots = []  # force no roots
        out = await tool.run(path=str(f))
        # Should accept because the file exists and no roots restrict it.
        assert "free" in out
