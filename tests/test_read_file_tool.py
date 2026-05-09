from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.tools.builtin.read_file_tool import ReadFileTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text("# Nested\nbody", encoding="utf-8")
    return tmp_path


class TestReadFileTool:
    async def test_reads_relative_path(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="hello.txt")
        assert "hello world" in out
        assert "hello.txt" in out

    async def test_reads_nested_path(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="sub/nested.md")
        assert "Nested" in out

    async def test_missing_path_returns_error(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="missing.txt")
        assert out.lower().startswith("error")
        assert "not found" in out.lower()

    async def test_blocks_path_traversal(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="../etc/passwd")
        assert "escapes workspace" in out.lower()

    async def test_blocks_directory(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="sub")
        assert "not a regular file" in out.lower()

    async def test_max_bytes_truncates(self, workspace: Path, tmp_path: Path) -> None:
        big = workspace / "big.txt"
        big.write_text("x" * 1000, encoding="utf-8")
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="big.txt", max_bytes=100)
        assert "showing first 100" in out

    async def test_empty_path_returns_error(self, workspace: Path) -> None:
        tool = ReadFileTool(workspace_root=workspace)
        out = await tool.run(path="")
        assert "required" in out.lower()

    def test_to_openai_tool_shape(self) -> None:
        schema = ReadFileTool().to_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "read_file"
        assert "path" in schema["function"]["parameters"]["properties"]
