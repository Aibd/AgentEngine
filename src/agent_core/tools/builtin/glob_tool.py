"""GlobTool — find files by glob pattern.

Modeled after claude-code-src `tools/GlobTool/`. Uses :mod:`pathlib`'s
``rglob`` for wildcard matching and sorts results by modification time
(newest first) so the model gets the most-relevant files when results are
truncated.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from agent_core.tools.base import Tool

logger = logging.getLogger(__name__)


GLOB_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": (
                "Glob pattern to match against file paths "
                "(e.g. '**/*.py', 'src/**/*.tsx', 'tests/test_*.py')."
            ),
        },
        "path": {
            "type": "string",
            "description": (
                "Directory to search in. Relative paths resolve against the "
                "workspace root. Omit to use the workspace root itself."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Max results returned (default 100, max 500).",
            "minimum": 1,
            "maximum": 500,
        },
    },
    "required": ["pattern"],
}


# Default directories we never recurse into. Same set as ripgrep's
# implicit ignore (".git") plus common JS/Python build outputs that bloat
# globs without being interesting.
_DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
    }
)


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files matching a glob pattern. Returns file paths relative to "
        "the search root, sorted by modification time (newest first). Use "
        "this when you know roughly what files exist (e.g. 'all *.tsx under "
        "src/') but don't want to grep their contents."
    )
    schema = GLOB_TOOL_SCHEMA
    timeout_seconds = 15.0
    max_result_chars = 16000
    result_summary_strategy = "head_tail"

    DEFAULT_LIMIT = 100
    MAX_LIMIT = 500

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] | None = None,
        *,
        extra_ignore_dirs: set[str] | None = None,
    ) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()
        self._ignore_dirs = _DEFAULT_IGNORE_DIRS | (extra_ignore_dirs or set())

    async def run(self, **kwargs: Any) -> str:
        pattern = str(kwargs.get("pattern", "")).strip()
        if not pattern:
            return "Error: 'pattern' is required."

        raw_path = kwargs.get("path")
        try:
            search_root = self._resolve_search_root(raw_path)
        except _GlobPathError as err:
            return f"Error: {err}"

        try:
            limit_in = int(kwargs.get("limit") or self.DEFAULT_LIMIT)
        except (TypeError, ValueError):
            return "Error: 'limit' must be an integer."
        limit = max(1, min(limit_in, self.MAX_LIMIT))

        try:
            matches = list(self._iter_matches(search_root, pattern, limit + 1))
        except (OSError, ValueError) as exc:
            return f"Error: glob failed: {exc}"

        truncated = len(matches) > limit
        if truncated:
            matches = matches[:limit]

        try:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            matches.sort()

        if not matches:
            return f"No files matched pattern {pattern!r} under {search_root}."

        rel_root = self._root
        lines: list[str] = []
        for path in matches:
            try:
                lines.append(str(path.relative_to(rel_root)))
            except ValueError:
                lines.append(str(path))

        header = f"# {len(lines)} match(es) for {pattern!r}"
        if truncated:
            header += f" (truncated to {limit}; narrow your pattern for more)"

        logger.info(
            "glob_tool_invoked pattern=%r matches=%d truncated=%s",
            pattern, len(lines), truncated,
        )
        return "\n".join([header, *lines])

    def _resolve_search_root(self, raw_path: Any) -> Path:
        if not raw_path:
            return self._root
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise _GlobPathError(f"cannot resolve path: {exc}") from exc
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise _GlobPathError(f"path escapes workspace root ({self._root})") from exc
        if not resolved.exists():
            raise _GlobPathError(f"directory does not exist: {resolved}")
        if not resolved.is_dir():
            raise _GlobPathError(f"not a directory: {resolved}")
        return resolved

    def _iter_matches(self, search_root: Path, pattern: str, hard_limit: int) -> Any:
        # ``rglob`` walks recursively but won't honor our ignore-dir set, so we
        # filter each yielded path by its parts. Bail out as soon as we have
        # ``hard_limit`` matches to keep latency bounded on huge trees.
        count = 0
        if pattern.startswith("/") or pattern.startswith("\\"):
            pattern = pattern.lstrip("/\\")

        # Decide whether to recurse: any pattern that doesn't already include
        # ``**`` and isn't a single-segment wildcard goes through rglob; keeps
        # behaviour close to claude-code-src.
        if "**" in pattern:
            iterator = search_root.glob(pattern)
        elif "/" in pattern or "\\" in pattern:
            iterator = search_root.glob(pattern)
        else:
            iterator = search_root.rglob(pattern)

        for path in iterator:
            if not path.is_file():
                continue
            if self._is_ignored(path, search_root):
                continue
            yield path
            count += 1
            if count >= hard_limit:
                return

    def _is_ignored(self, path: Path, search_root: Path) -> bool:
        try:
            rel = path.relative_to(search_root)
        except ValueError:
            return False
        return any(part in self._ignore_dirs for part in rel.parts)


class _GlobPathError(ValueError):
    """Internal error type so :meth:`run` can return uniform error strings."""
