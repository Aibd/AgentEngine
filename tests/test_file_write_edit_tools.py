from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.tools.builtin.file_edit_tool import FileEditTool
from agent_core.tools.builtin.file_write_tool import FileAccessTracker, FileWriteTool


class _RecordingTracker:
    """Trivial tracker for tests; mirrors the runtime's real implementation."""

    def __init__(self, read: set[Path] | None = None) -> None:
        self._read = set(read or [])
        self._written: list[Path] = []

    def has_read(self, path: Path) -> bool:
        return path in self._read

    def mark_read(self, path: Path) -> None:
        self._read.add(path)

    def mark_written(self, path: Path) -> None:
        self._written.append(path)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestFileWriteTool:
    async def test_creates_new_file(self, workspace: Path) -> None:
        tool = FileWriteTool(workspace_root=workspace)
        out = await tool.run(path="hello.txt", content="hi\n")
        assert "Created" in out
        assert (workspace / "hello.txt").read_text(encoding="utf-8") == "hi\n"

    async def test_creates_parent_dirs(self, workspace: Path) -> None:
        tool = FileWriteTool(workspace_root=workspace)
        out = await tool.run(path="a/b/c/d.txt", content="x")
        assert "Created" in out
        assert (workspace / "a" / "b" / "c" / "d.txt").exists()

    async def test_overwrite_requires_read_first(self, workspace: Path) -> None:
        existing = workspace / "exists.txt"
        existing.write_text("old", encoding="utf-8")
        tracker: FileAccessTracker = _RecordingTracker()
        tool = FileWriteTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(path="exists.txt", content="new")
        assert out.startswith("Error")
        assert "without reading it first" in out
        assert existing.read_text(encoding="utf-8") == "old"  # untouched

    async def test_overwrite_after_read_succeeds(self, workspace: Path) -> None:
        existing = workspace / "exists.txt"
        existing.write_text("old", encoding="utf-8")
        tracker = _RecordingTracker()
        tracker.mark_read(existing.resolve())
        tool = FileWriteTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(path="exists.txt", content="new\n")
        assert "Overwrote" in out
        assert existing.read_text(encoding="utf-8") == "new\n"

    async def test_overwrite_guard_can_be_disabled(self, workspace: Path) -> None:
        existing = workspace / "exists.txt"
        existing.write_text("old", encoding="utf-8")
        tool = FileWriteTool(
            workspace_root=workspace, require_read_before_overwrite=False
        )
        out = await tool.run(path="exists.txt", content="new")
        assert "Overwrote" in out

    async def test_path_escape_rejected(self, workspace: Path) -> None:
        tool = FileWriteTool(workspace_root=workspace)
        out = await tool.run(path="../escape.txt", content="x")
        assert "escapes workspace" in out

    async def test_missing_path(self, workspace: Path) -> None:
        tool = FileWriteTool(workspace_root=workspace)
        out = await tool.run(content="x")
        assert out.startswith("Error: 'path'")

    async def test_missing_content(self, workspace: Path) -> None:
        tool = FileWriteTool(workspace_root=workspace)
        out = await tool.run(path="hello.txt")
        assert out.startswith("Error: 'content'")

    async def test_appends_trailing_newline(self, workspace: Path) -> None:
        tool = FileWriteTool(workspace_root=workspace)
        await tool.run(path="x.txt", content="no newline")
        assert (workspace / "x.txt").read_text(encoding="utf-8") == "no newline\n"


class TestFileEditTool:
    async def test_replaces_unique_string(self, workspace: Path) -> None:
        target = workspace / "f.py"
        target.write_text("a = 1\nb = 2\n", encoding="utf-8")
        tracker = _RecordingTracker(read={target.resolve()})
        tool = FileEditTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(path="f.py", old_string="a = 1", new_string="a = 99")
        assert "Replaced 1" in out
        assert (workspace / "f.py").read_text(encoding="utf-8") == "a = 99\nb = 2\n"

    async def test_rejects_when_string_missing(self, workspace: Path) -> None:
        target = workspace / "f.py"
        target.write_text("a = 1\n", encoding="utf-8")
        tracker = _RecordingTracker(read={target.resolve()})
        tool = FileEditTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(path="f.py", old_string="not here", new_string="x")
        assert "old_string not found" in out

    async def test_rejects_ambiguous_match(self, workspace: Path) -> None:
        target = workspace / "f.py"
        target.write_text("x\nx\n", encoding="utf-8")
        tracker = _RecordingTracker(read={target.resolve()})
        tool = FileEditTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(path="f.py", old_string="x", new_string="y")
        assert "matches 2 times" in out
        # File should be untouched
        assert (workspace / "f.py").read_text(encoding="utf-8") == "x\nx\n"

    async def test_replace_all(self, workspace: Path) -> None:
        target = workspace / "f.py"
        target.write_text("foo bar foo baz foo\n", encoding="utf-8")
        tracker = _RecordingTracker(read={target.resolve()})
        tool = FileEditTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(
            path="f.py", old_string="foo", new_string="qux", replace_all=True
        )
        assert "Replaced 3" in out
        assert (workspace / "f.py").read_text(encoding="utf-8") == "qux bar qux baz qux\n"

    async def test_requires_read_first(self, workspace: Path) -> None:
        target = workspace / "f.py"
        target.write_text("a = 1\n", encoding="utf-8")
        tracker = _RecordingTracker()  # nothing read
        tool = FileEditTool(workspace_root=workspace, access_tracker=tracker)
        out = await tool.run(path="f.py", old_string="a = 1", new_string="a = 2")
        assert "without reading it first" in out
        assert (workspace / "f.py").read_text(encoding="utf-8") == "a = 1\n"

    async def test_no_op_rejected(self, workspace: Path) -> None:
        tool = FileEditTool(workspace_root=workspace, require_read_first=False)
        target = workspace / "f.py"
        target.write_text("x", encoding="utf-8")
        out = await tool.run(path="f.py", old_string="x", new_string="x")
        assert "identical" in out

    async def test_empty_old_string_rejected(self, workspace: Path) -> None:
        tool = FileEditTool(workspace_root=workspace, require_read_first=False)
        target = workspace / "f.py"
        target.write_text("x", encoding="utf-8")
        out = await tool.run(path="f.py", old_string="", new_string="y")
        assert "must not be empty" in out

    async def test_missing_file(self, workspace: Path) -> None:
        tool = FileEditTool(workspace_root=workspace, require_read_first=False)
        out = await tool.run(path="missing.py", old_string="a", new_string="b")
        assert "file not found" in out


class TestBuildDefaultTools:
    def test_constructs_full_set(self, workspace: Path) -> None:
        from agent_core.tools.builtin import build_default_tools

        tools = build_default_tools(workspace_root=workspace)
        names = sorted(t.name for t in tools)
        assert names == sorted(
            [
                "AskUserQuestion",
                "TodoWrite",
                "bash",
                "edit_file",
                "glob",
                "grep",
                "read_file",
                "write_file",
            ]
        )

    def test_include_filter(self, workspace: Path) -> None:
        from agent_core.tools.builtin import build_default_tools

        tools = build_default_tools(workspace_root=workspace, include=["read_file", "grep"])
        assert sorted(t.name for t in tools) == ["grep", "read_file"]

    def test_exclude_filter(self, workspace: Path) -> None:
        from agent_core.tools.builtin import build_default_tools

        tools = build_default_tools(workspace_root=workspace, exclude=["bash"])
        assert "bash" not in {t.name for t in tools}

    def test_unknown_name_raises(self, workspace: Path) -> None:
        from agent_core.tools.builtin import build_default_tools

        with pytest.raises(KeyError):
            build_default_tools(workspace_root=workspace, include=["nope"])

    def test_access_tracker_threaded_into_write_tools(self, workspace: Path) -> None:
        from agent_core.tools.builtin import FileEditTool, FileWriteTool, build_default_tools

        tracker = _RecordingTracker()
        tools = build_default_tools(workspace_root=workspace, access_tracker=tracker)
        write = next(t for t in tools if isinstance(t, FileWriteTool))
        edit = next(t for t in tools if isinstance(t, FileEditTool))
        assert write._tracker is tracker
        assert edit._tracker is tracker
