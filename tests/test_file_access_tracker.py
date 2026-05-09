from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.runtime.events import (
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
    TurnStarted,
)
from agentengine.runtime.file_access_tracker import TurnFileAccessTracker


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    return tmp_path


def _started(tool_name: str, *, call_id: str, args: dict[str, object]) -> ToolCallStarted:
    return ToolCallStarted(
        run_id="r",
        turn_id="t",
        tool_call_id=call_id,
        tool_name=tool_name,
        arguments=args,
    )


def _completed(tool_name: str, *, call_id: str) -> ToolCallCompleted:
    return ToolCallCompleted(
        run_id="r",
        turn_id="t",
        tool_call_id=call_id,
        tool_name=tool_name,
        result_summary="ok",
        elapsed_seconds=0.01,
    )


class TestTurnFileAccessTracker:
    def test_read_followed_by_completed_marks_path(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(_started("read_file", call_id="1", args={"path": "src/a.py"}))
        tracker.observe(_completed("read_file", call_id="1"))
        assert tracker.has_read(workspace / "src" / "a.py")

    def test_only_completed_with_no_started_is_inert(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        # Completed without prior Started → no args cached → nothing tracked
        tracker.observe(_completed("read_file", call_id="ghost"))
        assert not tracker.has_read(workspace / "src" / "a.py")

    def test_relative_and_absolute_paths_normalize(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        absolute = str((workspace / "src" / "a.py").resolve())
        tracker.observe(_started("read_file", call_id="1", args={"path": absolute}))
        tracker.observe(_completed("read_file", call_id="1"))
        # Relative form should resolve to the same path.
        assert tracker.has_read(Path("src/a.py"))
        assert tracker.has_read(workspace / "src" / "a.py")

    def test_write_marks_both_read_and_written(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(_started("write_file", call_id="1", args={"path": "new.txt"}))
        tracker.observe(_completed("write_file", call_id="1"))
        snap = tracker.snapshot()
        new_file = str((workspace / "new.txt").resolve())
        assert new_file in snap["written"]
        assert new_file in snap["read"]

    def test_glob_marks_explicit_path_argument(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(
            _started("glob", call_id="1", args={"pattern": "**/*.py", "path": "src"})
        )
        tracker.observe(_completed("glob", call_id="1"))
        assert tracker.has_read(workspace / "src")

    def test_grep_without_path_argument_is_noop(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(_started("grep", call_id="1", args={"pattern": "foo"}))
        tracker.observe(_completed("grep", call_id="1"))
        # No specific path was given → nothing to mark as read.
        assert tracker.snapshot() == {"read": [], "written": []}

    def test_unrelated_tool_ignored(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(_started("bash", call_id="1", args={"command": "ls"}))
        tracker.observe(_completed("bash", call_id="1"))
        assert tracker.snapshot() == {"read": [], "written": []}

    def test_unrelated_event_ignored(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(TurnStarted(run_id="r", turn_id="t", turn=1))
        assert tracker.snapshot() == {"read": [], "written": []}

    def test_failed_tool_call_does_not_mark(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tracker.observe(_started("read_file", call_id="1", args={"path": "src/a.py"}))
        # Simulate failure: emit ToolCallFailed instead of Completed
        tracker.observe(
            ToolCallFailed(
                run_id="r",
                turn_id="t",
                tool_call_id="1",
                tool_name="read_file",
                error_type="TimeoutError",
                error_message="timed out",
                elapsed_seconds=0.01,
            )
        )
        # The pending args remain stashed but ``has_read`` only consults the
        # confirmed read set, which is still empty.
        assert not tracker.has_read(workspace / "src" / "a.py")

    def test_mark_written_is_idempotent(self, workspace: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        target = workspace / "x.txt"
        tracker.mark_written(target)
        tracker.mark_written(target)
        snap = tracker.snapshot()
        assert snap["written"].count(str(target.resolve())) == 1

    def test_has_read_for_path_outside_root(self, workspace: Path, tmp_path: Path) -> None:
        tracker = TurnFileAccessTracker(workspace_root=workspace)
        outside = tmp_path.parent / "outside.txt"
        # Never observed; should always return False.
        assert not tracker.has_read(outside)


class TestTrackerEndToEnd:
    """Drive the tracker through a sequence resembling a real ReAct loop."""

    def test_read_then_write_allowed_after_read(self, workspace: Path) -> None:
        from agentengine.tools.builtin.file_write_tool import FileWriteTool

        existing = workspace / "src" / "a.py"
        tracker = TurnFileAccessTracker(workspace_root=workspace)

        # Step 1: agent reads the file.
        tracker.observe(_started("read_file", call_id="r1", args={"path": "src/a.py"}))
        tracker.observe(_completed("read_file", call_id="r1"))

        # Step 2: agent writes back. Tool should accept the overwrite.
        tool = FileWriteTool(workspace_root=workspace, access_tracker=tracker)

        async def _go() -> str:
            return await tool.run(path="src/a.py", content="b")

        import asyncio

        out = asyncio.run(_go())
        assert "Overwrote" in out
        assert existing.read_text(encoding="utf-8") == "b\n"

    def test_write_without_prior_read_blocked(self, workspace: Path) -> None:
        from agentengine.tools.builtin.file_write_tool import FileWriteTool

        tracker = TurnFileAccessTracker(workspace_root=workspace)
        tool = FileWriteTool(workspace_root=workspace, access_tracker=tracker)

        async def _go() -> str:
            return await tool.run(path="src/a.py", content="b")

        import asyncio

        out = asyncio.run(_go())
        assert "without reading it first" in out
        # Original content untouched
        assert (workspace / "src" / "a.py").read_text(encoding="utf-8") == "a"
