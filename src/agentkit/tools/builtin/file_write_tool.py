"""FileWriteTool — create or overwrite a file in the workspace.

Modeled after claude-code-src `tools/FileWriteTool/`. The "must read first"
invariant from the TS version is enforced via :class:`FileAccessTracker`,
which the runtime injects so we can tell whether the agent has called
``read_file`` on the path during this turn before letting it overwrite.

Behaviour:
- creates parent directories if missing
- refuses to overwrite an existing file unless the agent read it first
  (mirrors claude-code's "you didn't see the file you're about to clobber"
  rule) — toggle with ``require_read_before_overwrite=False`` if the agent
  spec doesn't want the guardrail
- writes UTF-8 with newline normalisation
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol

from agentkit.tools.base import Tool

logger = logging.getLogger(__name__)


FILE_WRITE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to write. Relative paths resolve against the workspace "
                "root. Parent directories are created if missing."
            ),
        },
        "content": {
            "type": "string",
            "description": "Full file contents to write (UTF-8).",
        },
    },
    "required": ["path", "content"],
}


class FileAccessTracker(Protocol):
    """Tracks which files the agent has read this turn.

    The runtime can supply a real implementation that inspects the message
    history; tests pass a tiny stub. Keeping this as a Protocol means we
    don't reach across the runtime boundary just to enforce one invariant.
    """

    def has_read(self, path: Path) -> bool: ...

    def mark_written(self, path: Path) -> None: ...


class _NullTracker:
    def has_read(self, path: Path) -> bool:  # noqa: D401 - intentional no-op
        return True

    def mark_written(self, path: Path) -> None:
        return None


class FileWriteTool(Tool):
    name = "write_file"
    description = (
        "Write content to a file in the workspace, creating it if needed. "
        "If the file already exists you must read it first (the runtime tracks "
        "this) so you don't accidentally clobber unseen changes. Prefer the "
        "edit_file tool when you only need to change part of a file."
    )
    schema = FILE_WRITE_TOOL_SCHEMA
    timeout_seconds = 10.0
    max_result_chars = 2000
    is_destructive = True

    MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MiB

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] | None = None,
        *,
        access_tracker: FileAccessTracker | None = None,
        require_read_before_overwrite: bool = True,
    ) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()
        self._tracker: FileAccessTracker = access_tracker or _NullTracker()
        self._require_read = require_read_before_overwrite

    async def run(self, **kwargs: Any) -> str:
        raw_path = str(kwargs.get("path", "")).strip()
        if not raw_path:
            return "Error: 'path' is required."
        if "content" not in kwargs:
            return "Error: 'content' is required."
        content = kwargs.get("content")
        if not isinstance(content, str):
            return "Error: 'content' must be a string."

        encoded_size = len(content.encode("utf-8", errors="replace"))
        if encoded_size > self.MAX_CONTENT_BYTES:
            return (
                f"Error: content too large ({encoded_size} bytes); "
                f"max is {self.MAX_CONTENT_BYTES}."
            )

        try:
            target = self._resolve_target(raw_path)
        except _FileWritePathError as err:
            return f"Error: {err}"

        already_exists = target.exists()
        if already_exists:
            if not target.is_file():
                return f"Error: not a regular file: {target}"
            if self._require_read and not self._tracker.has_read(target):
                return (
                    f"Error: refusing to overwrite {target.relative_to(self._root)} "
                    "without reading it first. Call read_file on the path before write_file."
                )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            normalized = content if content.endswith("\n") or content == "" else content + "\n"
            target.write_text(normalized, encoding="utf-8", newline="\n")
        except OSError as exc:
            return f"Error: write failed: {exc}"

        self._tracker.mark_written(target)
        rel = target.relative_to(self._root)
        verb = "Overwrote" if already_exists else "Created"
        size = target.stat().st_size
        logger.info(
            "file_write_tool_invoked path=%s bytes=%d existed=%s",
            rel, size, already_exists,
        )
        return f"{verb} {rel} ({size} bytes)."

    def _resolve_target(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise _FileWritePathError(f"cannot resolve path: {exc}") from exc
        # ``resolve()`` returns the would-be absolute path even if it doesn't
        # exist, so this check still works for new files.
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise _FileWritePathError(
                f"path escapes workspace root ({self._root})"
            ) from exc
        return resolved


class _FileWritePathError(ValueError):
    """Internal error type for uniform error-string returns."""
