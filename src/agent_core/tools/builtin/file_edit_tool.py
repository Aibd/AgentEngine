"""FileEditTool — exact-string replacement in a single file.

Modeled after claude-code-src `tools/FileEditTool/`. The model supplies an
``old_string`` literal that must appear exactly once in the file (unless
``replace_all`` is set) and a ``new_string`` to put in its place. We reject
writes when:

- the file hasn't been read this turn (matches FileWriteTool's invariant)
- ``old_string`` doesn't exist in the file
- ``old_string`` appears multiple times and ``replace_all`` is False
- ``old_string`` and ``new_string`` are equal (no-op)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from agent_core.tools.base import Tool
from agent_core.tools.builtin.file_write_tool import (
    FileAccessTracker,
    _NullTracker,
)

logger = logging.getLogger(__name__)


FILE_EDIT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to the file to edit. Relative paths resolve against the "
                "workspace root."
            ),
        },
        "old_string": {
            "type": "string",
            "description": "The exact text to replace. Must match the file verbatim.",
        },
        "new_string": {
            "type": "string",
            "description": "The replacement text. May be empty to delete the match.",
        },
        "replace_all": {
            "type": "boolean",
            "description": (
                "If true, replace every occurrence of old_string. If false "
                "(default), require exactly one occurrence."
            ),
        },
    },
    "required": ["path", "old_string", "new_string"],
}


class FileEditTool(Tool):
    name = "edit_file"
    description = (
        "Replace one or more occurrences of an exact string in a file. The "
        "file must have been read this turn so you know its current contents. "
        "Use replace_all=true to rename across the whole file; otherwise "
        "old_string must be unique."
    )
    schema = FILE_EDIT_TOOL_SCHEMA
    timeout_seconds = 10.0
    max_result_chars = 4000
    is_destructive = True

    MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] | None = None,
        *,
        access_tracker: FileAccessTracker | None = None,
        require_read_first: bool = True,
    ) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()
        self._tracker: FileAccessTracker = access_tracker or _NullTracker()
        self._require_read = require_read_first

    async def run(self, **kwargs: Any) -> str:
        raw_path = str(kwargs.get("path", "")).strip()
        if not raw_path:
            return "Error: 'path' is required."
        if "old_string" not in kwargs or "new_string" not in kwargs:
            return "Error: 'old_string' and 'new_string' are required."

        old_string = kwargs.get("old_string")
        new_string = kwargs.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return "Error: 'old_string' and 'new_string' must be strings."
        if old_string == new_string:
            return "Error: 'old_string' and 'new_string' are identical (no-op)."
        if old_string == "":
            return "Error: 'old_string' must not be empty."
        replace_all = bool(kwargs.get("replace_all", False))

        try:
            target = self._resolve_target(raw_path)
        except _FileEditPathError as err:
            return f"Error: {err}"

        if not target.exists():
            return f"Error: file not found: {target.relative_to(self._root)}"
        if not target.is_file():
            return f"Error: not a regular file: {target.relative_to(self._root)}"
        if target.stat().st_size > self.MAX_FILE_BYTES:
            return (
                f"Error: file too large for in-memory edit "
                f"(>{self.MAX_FILE_BYTES} bytes): {target.relative_to(self._root)}"
            )
        if self._require_read and not self._tracker.has_read(target):
            return (
                f"Error: refusing to edit {target.relative_to(self._root)} "
                "without reading it first. Call read_file on the path before edit_file."
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: file is not valid UTF-8: {target.relative_to(self._root)}"
        except OSError as exc:
            return f"Error: read failed: {exc}"

        occurrences = content.count(old_string)
        if occurrences == 0:
            return (
                f"Error: old_string not found in {target.relative_to(self._root)}. "
                "Make sure indentation, whitespace, and line endings match exactly."
            )
        if occurrences > 1 and not replace_all:
            return (
                f"Error: old_string matches {occurrences} times in "
                f"{target.relative_to(self._root)}; pass replace_all=true or "
                "include more surrounding context to make it unique."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = occurrences
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        try:
            target.write_text(new_content, encoding="utf-8", newline="\n")
        except OSError as exc:
            return f"Error: write failed: {exc}"

        self._tracker.mark_written(target)
        rel = target.relative_to(self._root)
        logger.info(
            "file_edit_tool_invoked path=%s replaced=%d replace_all=%s",
            rel, replaced, replace_all,
        )
        if replaced == 1:
            return f"Replaced 1 occurrence in {rel}."
        return f"Replaced {replaced} occurrences in {rel}."

    def _resolve_target(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise _FileEditPathError(f"cannot resolve path: {exc}") from exc
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise _FileEditPathError(
                f"path escapes workspace root ({self._root})"
            ) from exc
        return resolved


class _FileEditPathError(ValueError):
    """Internal error type for uniform error-string returns."""
