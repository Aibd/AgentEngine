"""GrepTool — search file contents by regex.

Modeled after claude-code-src `tools/GrepTool/`. Prefers ``ripgrep`` (``rg``)
when it's on PATH because it's an order of magnitude faster on large trees;
falls back to a pure-Python ``re`` walker so the tool always works.

Output modes mirror the TS version:
- ``files_with_matches`` (default): one path per matching file
- ``content``: matching lines with line numbers
- ``count``: per-file match counts
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from agentkit.tools.base import Tool

logger = logging.getLogger(__name__)


GREP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Regular expression pattern to search for (Python re / ripgrep syntax).",
        },
        "path": {
            "type": "string",
            "description": (
                "Directory or file to search in. Relative paths resolve against "
                "the workspace root. Omit to search the workspace root."
            ),
        },
        "glob": {
            "type": "string",
            "description": (
                "Optional glob (e.g. '*.py', '**/*.tsx') restricting which "
                "files are searched."
            ),
        },
        "output_mode": {
            "type": "string",
            "enum": ["files_with_matches", "content", "count"],
            "description": "How results are formatted (default: files_with_matches).",
        },
        "case_insensitive": {
            "type": "boolean",
            "description": "Case-insensitive match (default false).",
        },
        "limit": {
            "type": "integer",
            "description": "Max output lines (default 200, max 1000).",
            "minimum": 1,
            "maximum": 1000,
        },
    },
    "required": ["pattern"],
}


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

# Skip files larger than this in the Python fallback. Ripgrep handles its own
# size heuristics. 2 MiB is generous for source code, cheap to scan.
_MAX_FILE_BYTES = 2 * 1024 * 1024


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search file contents by regular expression. Uses ripgrep when "
        "available; otherwise falls back to a Python regex walker. Returns "
        "matching paths, lines with line numbers, or per-file counts depending "
        "on the requested output_mode."
    )
    schema = GREP_TOOL_SCHEMA
    timeout_seconds = 30.0
    max_result_chars = 16000
    result_summary_strategy = "head_tail"

    DEFAULT_LIMIT = 200
    MAX_LIMIT = 1000

    def __init__(
        self,
        workspace_root: str | os.PathLike[str] | None = None,
        *,
        prefer_ripgrep: bool = True,
    ) -> None:
        if workspace_root is None:
            root = Path(os.getenv("AGENT_WORKSPACE_ROOT", os.getcwd()))
        else:
            root = Path(workspace_root)
        self._root = root.resolve()
        self._rg_path = shutil.which("rg") if prefer_ripgrep else None

    async def run(self, **kwargs: Any) -> str:
        pattern = str(kwargs.get("pattern", ""))
        if not pattern:
            return "Error: 'pattern' is required."

        try:
            re.compile(pattern)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"

        try:
            search_root = self._resolve_search_root(kwargs.get("path"))
        except _GrepPathError as err:
            return f"Error: {err}"

        glob = kwargs.get("glob")
        glob_str = str(glob).strip() if glob else None
        output_mode = str(kwargs.get("output_mode") or "files_with_matches")
        if output_mode not in {"files_with_matches", "content", "count"}:
            return f"Error: unknown output_mode {output_mode!r}."
        case_insensitive = bool(kwargs.get("case_insensitive"))

        try:
            limit_in = int(kwargs.get("limit") or self.DEFAULT_LIMIT)
        except (TypeError, ValueError):
            return "Error: 'limit' must be an integer."
        limit = max(1, min(limit_in, self.MAX_LIMIT))

        if self._rg_path is not None:
            try:
                rendered = await self._search_ripgrep(
                    pattern=pattern,
                    search_root=search_root,
                    glob=glob_str,
                    output_mode=output_mode,
                    case_insensitive=case_insensitive,
                    limit=limit,
                )
                if rendered is not None:
                    return rendered
            except OSError as exc:
                logger.warning("grep_tool_ripgrep_failed err=%s — falling back", exc)

        return self._search_python(
            pattern=pattern,
            search_root=search_root,
            glob=glob_str,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            limit=limit,
        )

    def _resolve_search_root(self, raw_path: Any) -> Path:
        if not raw_path:
            return self._root
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise _GrepPathError(f"cannot resolve path: {exc}") from exc
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise _GrepPathError(f"path escapes workspace root ({self._root})") from exc
        if not resolved.exists():
            raise _GrepPathError(f"path does not exist: {resolved}")
        return resolved

    async def _search_ripgrep(
        self,
        *,
        pattern: str,
        search_root: Path,
        glob: str | None,
        output_mode: str,
        case_insensitive: bool,
        limit: int,
    ) -> str | None:
        assert self._rg_path is not None
        argv: list[str] = [self._rg_path, "--no-config", "--color=never"]
        if case_insensitive:
            argv.append("-i")
        if output_mode == "files_with_matches":
            argv.append("-l")
        elif output_mode == "count":
            argv.append("-c")
        else:  # content
            argv.extend(["-n", "-H"])
        if glob:
            argv.extend(["-g", glob])
        argv.extend(["-e", pattern, str(search_root)])

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.terminate()
            await proc.wait()
            return f"Error: ripgrep timed out after {self.timeout_seconds:.0f}s."

        # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
        if proc.returncode == 1:
            return self._format_no_matches(pattern, search_root)
        if proc.returncode not in (0, 1):
            stderr = stderr_b.decode("utf-8", errors="replace").strip()
            # Don't surface rg failures as tool errors; let caller fall back.
            logger.warning(
                "grep_tool_ripgrep_exit code=%s err=%s", proc.returncode, stderr[:200]
            )
            return None

        text = stdout_b.decode("utf-8", errors="replace")
        return self._render_lines(
            text.splitlines(),
            pattern=pattern,
            output_mode=output_mode,
            search_root=search_root,
            limit=limit,
        )

    def _search_python(
        self,
        *,
        pattern: str,
        search_root: Path,
        glob: str | None,
        output_mode: str,
        case_insensitive: bool,
        limit: int,
    ) -> str:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)

        candidates = self._iter_files(search_root, glob)

        per_file_count: dict[Path, int] = {}
        files_with_matches: list[Path] = []
        content_lines: list[str] = []
        total_lines = 0

        for path in candidates:
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            file_match_count = 0
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    file_match_count += 1
                    if output_mode == "content":
                        try:
                            rel = path.relative_to(self._root)
                        except ValueError:
                            rel = path
                        content_lines.append(f"{rel}:{line_no}:{line}")
                        total_lines += 1
                        if total_lines >= limit:
                            break
            if file_match_count > 0:
                files_with_matches.append(path)
                per_file_count[path] = file_match_count
            if output_mode == "content" and total_lines >= limit:
                break
            if (
                output_mode in {"files_with_matches", "count"}
                and len(files_with_matches) >= limit
            ):
                break

        if output_mode == "content":
            if not content_lines:
                return self._format_no_matches(pattern, search_root)
            header = f"# {len(content_lines)} match(es) for {pattern!r}"
            if total_lines >= limit:
                header += f" (truncated to {limit}; narrow your pattern)"
            return "\n".join([header, *content_lines])

        if output_mode == "count":
            if not files_with_matches:
                return self._format_no_matches(pattern, search_root)
            lines = [
                f"{self._relative(path)}:{per_file_count[path]}"
                for path in files_with_matches
            ]
            header = f"# match counts for {pattern!r} across {len(lines)} file(s)"
            return "\n".join([header, *lines])

        # files_with_matches
        if not files_with_matches:
            return self._format_no_matches(pattern, search_root)
        lines = [self._relative(path) for path in files_with_matches]
        header = f"# {len(lines)} file(s) match {pattern!r}"
        return "\n".join([header, *lines])

    def _iter_files(self, search_root: Path, glob: str | None) -> Iterable[Path]:
        if search_root.is_file():
            yield search_root
            return
        iterator = search_root.rglob(glob) if glob else search_root.rglob("*")
        for path in iterator:
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(search_root)
            except ValueError:
                continue
            if any(part in _DEFAULT_IGNORE_DIRS for part in rel.parts):
                continue
            yield path

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._root))
        except ValueError:
            return str(path)

    def _format_no_matches(self, pattern: str, search_root: Path) -> str:
        try:
            rel = search_root.relative_to(self._root)
        except ValueError:
            rel = search_root
        return f"No matches for {pattern!r} under {rel}."

    def _render_lines(
        self,
        lines: list[str],
        *,
        pattern: str,
        output_mode: str,
        search_root: Path,
        limit: int,
    ) -> str:
        if not lines:
            return self._format_no_matches(pattern, search_root)

        truncated = len(lines) > limit
        if truncated:
            lines = lines[:limit]

        normalized: list[str] = []
        for raw in lines:
            try:
                rel = self._normalize_rg_line(raw)
            except ValueError:
                rel = raw
            normalized.append(rel)

        if output_mode == "files_with_matches":
            header = f"# {len(normalized)} file(s) match {pattern!r}"
        elif output_mode == "count":
            header = f"# match counts for {pattern!r} across {len(normalized)} file(s)"
        else:
            header = f"# {len(normalized)} match(es) for {pattern!r}"
        if truncated:
            header += f" (truncated to {limit})"
        return "\n".join([header, *normalized])

    def _normalize_rg_line(self, line: str) -> str:
        # ripgrep prints absolute paths because we passed an absolute search
        # root; relativize them to keep token cost manageable.
        root_str = str(self._root)
        if line.startswith(root_str):
            return line[len(root_str) + 1 :]
        return line


class _GrepPathError(ValueError):
    """Internal error type so :meth:`run` can return uniform error strings."""
