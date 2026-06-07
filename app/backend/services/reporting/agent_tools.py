"""Agent tools for on-demand full-content access.

The report-generation prompt always carries a bounded preview of
each uploaded file. When the model needs more than the preview, it
calls these tools to read additional ranges from the parsed artifacts
on disk (``data/parsed/<sha>/text.txt`` and ``sheet_<n>.csv``) or
to run SQL over a sheet via DuckDB.

Every tool is scoped to ``allowed_file_ids`` (the file_ids the
current report job has been authorised to read). Calls referencing
other file_ids return an ``error`` payload instead of raising, so
the LLM can recover with another tool call rather than crashing
the whole turn.

Tool schemas follow the OpenAI function-calling spec and are
returned by :func:`tool_specs` so the orchestrator can hand them
to ``LLMClient.chat(tools=...)``.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.backend.services.reporting.file_store import ReportFileStore


logger = logging.getLogger(__name__)


TOOL_LIST_UPLOADED = "list_uploaded_files"
TOOL_READ_TEXT = "read_file_text"
TOOL_READ_SHEET = "read_sheet"
TOOL_QUERY_SHEET = "query_sheet"

MAX_TEXT_CHARS = 8000
MAX_SHEET_ROWS = 500
MAX_QUERY_ROWS = 500


def tool_specs() -> list[dict[str, Any]]:
    """Return OpenAI function-calling tool specs for all report tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_LIST_UPLOADED,
                "description": (
                    "列出当前报告可访问的所有上传文件，含 file_id、类型、大小、"
                    "页数/页签数、sheet 列表与每个 sheet 的行列规模。生成报告前先调用一次。"
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_READ_TEXT,
                "description": (
                    "读取一个 pdf/docx/pptx/md/txt 文件已抽取的全量文本片段。"
                    f"每次返回 ≤{MAX_TEXT_CHARS} 字符；超出请通过 offset+length 翻页。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "上传文件的 file_id"},
                        "offset": {
                            "type": "integer",
                            "description": "字符偏移（从 0 开始）",
                            "minimum": 0,
                            "default": 0,
                        },
                        "length": {
                            "type": "integer",
                            "description": f"要读取的字符数，最大 {MAX_TEXT_CHARS}",
                            "minimum": 1,
                            "maximum": MAX_TEXT_CHARS,
                            "default": MAX_TEXT_CHARS,
                        },
                    },
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_READ_SHEET,
                "description": (
                    "按行范围读取 xlsx/csv 文件的某个 sheet 的完整内容。"
                    f"每次返回 ≤{MAX_SHEET_ROWS} 行（不含表头）；超过用 row_start/row_end 翻页。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "sheet_name": {
                            "type": "string",
                            "description": "sheet 名（先用 list_uploaded_files 查询）；csv 文件传 sheet 文件名或留空",
                        },
                        "row_start": {
                            "type": "integer",
                            "description": "起始数据行（0 开始，不含表头）",
                            "minimum": 0,
                            "default": 0,
                        },
                        "row_end": {
                            "type": "integer",
                            "description": f"结束数据行（独占）；与 row_start 之差 ≤ {MAX_SHEET_ROWS}",
                            "minimum": 1,
                        },
                    },
                    "required": ["file_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_QUERY_SHEET,
                "description": (
                    "对一个 sheet 运行只读 SQL（DuckDB 方言）。表名固定为 `t`，列名即表头。"
                    f"返回前 {MAX_QUERY_ROWS} 行结果。用于聚合、TOP N、同环比等计算。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "sql": {
                            "type": "string",
                            "description": "SELECT-only SQL，表名 `t`，例如 SELECT period, SUM(revenue) FROM t GROUP BY period ORDER BY period",
                        },
                    },
                    "required": ["file_id", "sql"],
                    "additionalProperties": False,
                },
            },
        },
    ]


@dataclass(slots=True)
class ToolContext:
    store: ReportFileStore
    allowed_file_ids: frozenset[str]
    project_root: Path


class ReportAgentTools:
    """Stateful wrapper that dispatches tool calls against a ReportFileStore.

    Construct one per report job with the list of file_ids the LLM is
    allowed to read.
    """

    def __init__(
        self,
        *,
        store: ReportFileStore,
        allowed_file_ids: Iterable[str],
        project_root: Path,
    ) -> None:
        self._ctx = ToolContext(
            store=store,
            allowed_file_ids=frozenset(allowed_file_ids),
            project_root=project_root,
        )

    @property
    def names(self) -> set[str]:
        return {TOOL_LIST_UPLOADED, TOOL_READ_TEXT, TOOL_READ_SHEET, TOOL_QUERY_SHEET}

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == TOOL_LIST_UPLOADED:
                return await self._list_uploaded()
            if name == TOOL_READ_TEXT:
                return await self._read_text(arguments)
            if name == TOOL_READ_SHEET:
                return await self._read_sheet(arguments)
            if name == TOOL_QUERY_SHEET:
                return await self._query_sheet(arguments)
        except Exception as exc:  # tools must never crash the agent loop
            logger.exception("report_tool_failed name=%s", name)
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"error": f"unknown tool: {name}"}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _list_uploaded(self) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for file_id in sorted(self._ctx.allowed_file_ids):
            record = await self._ctx.store.aget(file_id)
            if record is None:
                continue
            parsed = record.parsed
            sheets = [
                {
                    "name": sheet.name,
                    "row_count": sheet.row_count,
                    "column_count": sheet.column_count,
                    "headers": sheet.headers,
                }
                for sheet in parsed.sheets
            ]
            files.append(
                {
                    "file_id": file_id,
                    "filename": record.filename,
                    "kind": parsed.kind,
                    "size_bytes": record.size_bytes,
                    "page_count": parsed.page_count,
                    "text_total_chars": parsed.text_total_chars,
                    "sheets": sheets,
                }
            )
        return {"files": files}

    async def _read_text(self, args: dict[str, Any]) -> dict[str, Any]:
        file_id = self._require_allowed(args.get("file_id"))
        if isinstance(file_id, dict):
            return file_id
        offset = max(0, int(args.get("offset") or 0))
        length = min(MAX_TEXT_CHARS, max(1, int(args.get("length") or MAX_TEXT_CHARS)))

        record = await self._ctx.store.aget(file_id)
        if record is None:
            return {"error": f"file_id not found: {file_id}"}

        parsed = record.parsed
        text = await self._load_full_text(parsed)
        if not text:
            return {
                "file_id": file_id,
                "filename": record.filename,
                "text": "",
                "total_chars": 0,
                "offset": offset,
                "next_offset": None,
                "warning": "no extractable text (scanned image / empty document)",
            }
        chunk = text[offset : offset + length]
        next_offset = offset + len(chunk) if offset + len(chunk) < len(text) else None
        return {
            "file_id": file_id,
            "filename": record.filename,
            "text": chunk,
            "total_chars": len(text),
            "offset": offset,
            "next_offset": next_offset,
        }

    async def _read_sheet(self, args: dict[str, Any]) -> dict[str, Any]:
        file_id = self._require_allowed(args.get("file_id"))
        if isinstance(file_id, dict):
            return file_id
        record = await self._ctx.store.aget(file_id)
        if record is None:
            return {"error": f"file_id not found: {file_id}"}
        sheet_path, sheet_name = self._resolve_sheet(record.parsed, args.get("sheet_name"))
        if sheet_path is None:
            return {"error": f"sheet not found: {args.get('sheet_name')!r}; available: {[s.name for s in record.parsed.sheets]}"}

        row_start = max(0, int(args.get("row_start") or 0))
        row_end_raw = args.get("row_end")
        row_end = (
            int(row_end_raw)
            if row_end_raw is not None
            else row_start + MAX_SHEET_ROWS
        )
        if row_end - row_start > MAX_SHEET_ROWS:
            row_end = row_start + MAX_SHEET_ROWS
        if row_end <= row_start:
            return {"error": "row_end must be greater than row_start"}

        headers, rows, total_rows = await self._read_csv_range(sheet_path, row_start, row_end)
        next_row = row_end if row_end < total_rows else None
        return {
            "file_id": file_id,
            "filename": record.filename,
            "sheet_name": sheet_name,
            "headers": headers,
            "rows": rows,
            "row_start": row_start,
            "row_end": row_start + len(rows),
            "total_rows": total_rows,
            "next_row": next_row,
        }

    async def _query_sheet(self, args: dict[str, Any]) -> dict[str, Any]:
        file_id = self._require_allowed(args.get("file_id"))
        if isinstance(file_id, dict):
            return file_id
        sql = str(args.get("sql") or "").strip()
        if not sql:
            return {"error": "sql is required"}
        if not _is_read_only_sql(sql):
            return {"error": "only SELECT / WITH queries are allowed"}

        record = await self._ctx.store.aget(file_id)
        if record is None:
            return {"error": f"file_id not found: {file_id}"}
        sheet_path, sheet_name = self._resolve_sheet(record.parsed, args.get("sheet_name"))
        if sheet_path is None:
            return {"error": f"sheet not found: {args.get('sheet_name')!r}; available: {[s.name for s in record.parsed.sheets]}"}

        try:
            import duckdb  # type: ignore[import-not-found]
        except Exception as exc:
            return {"error": f"duckdb unavailable: {type(exc).__name__}"}

        try:
            conn = duckdb.connect()
            # ``read_csv_auto`` doesn't accept prepared parameters in
            # CREATE VIEW context, so we inline the path. The path was
            # resolved from a sha256-addressed parsed artifact, never
            # from user input, so this isn't an injection vector.
            sheet_str = str(sheet_path).replace("'", "''")
            conn.execute(
                f"CREATE TEMP VIEW t AS SELECT * FROM read_csv_auto('{sheet_str}', header=True, all_varchar=False)"
            )
            cursor = conn.execute(sql)
            arrow_fn = getattr(cursor, "to_arrow_table", None) or cursor.fetch_arrow_table
            result = arrow_fn()
        except Exception as exc:
            return {"error": f"sql failed: {type(exc).__name__}: {exc}"}
        finally:
            try:
                conn.close()  # type: ignore[name-defined]
            except Exception:
                pass

        columns = result.column_names
        rows_full = result.to_pylist()
        truncated = len(rows_full) > MAX_QUERY_ROWS
        rows = rows_full[:MAX_QUERY_ROWS]
        return {
            "file_id": file_id,
            "filename": record.filename,
            "sheet_name": sheet_name,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_allowed(self, file_id: Any) -> str | dict[str, Any]:
        if not isinstance(file_id, str) or not file_id.strip():
            return {"error": "file_id is required"}
        if file_id not in self._ctx.allowed_file_ids:
            return {"error": f"file_id not authorised for this report: {file_id}"}
        return file_id

    async def _load_full_text(self, parsed: Any) -> str:
        """Return the full extracted text, loading from disk if necessary."""
        preview = parsed.text_preview or ""
        if not parsed.text_full_path:
            return preview
        candidate = self._ctx.project_root / parsed.text_full_path
        if not candidate.exists():
            return preview
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return preview

    def _resolve_sheet(self, parsed: Any, requested: Any) -> tuple[Path | None, str]:
        sheets = parsed.sheets or []
        if not sheets:
            return None, ""
        name = (requested or "").strip() if isinstance(requested, str) else ""
        sheet = None
        if name:
            for candidate in sheets:
                if candidate.name == name:
                    sheet = candidate
                    break
            if sheet is None:
                return None, name
        else:
            sheet = sheets[0]
        if not sheet.full_csv_path:
            return None, sheet.name
        return self._ctx.project_root / sheet.full_csv_path, sheet.name

    async def _read_csv_range(
        self,
        path: Path,
        row_start: int,
        row_end: int,
    ) -> tuple[list[str], list[list[str]], int]:
        headers: list[str] = []
        rows: list[list[str]] = []
        total = 0
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader):
                if index == 0:
                    headers = list(row)
                    continue
                data_index = index - 1
                if data_index >= row_start and data_index < row_end:
                    rows.append(list(row))
                total = data_index + 1
        return headers, rows, total


_SELECT_LIKE_PREFIX = ("select", "with")
_FORBIDDEN_SQL_TOKENS = (
    "insert ",
    "update ",
    "delete ",
    "drop ",
    "create ",
    "alter ",
    "attach ",
    "copy ",
    "pragma ",
    "load ",
    "install ",
    "vacuum ",
)


def _is_read_only_sql(sql: str) -> bool:
    lowered = sql.lower().lstrip()
    if not lowered.startswith(_SELECT_LIKE_PREFIX):
        return False
    # Block obvious mutation tokens. Comments and string literals could
    # smuggle these through; for the MVP we accept that risk because the
    # DB itself is an in-memory DuckDB instance owned by this process.
    return not any(token in lowered for token in _FORBIDDEN_SQL_TOKENS)


def format_tool_result(payload: dict[str, Any]) -> str:
    """Serialise a tool result for the `tool` message content slot."""
    return json.dumps(payload, ensure_ascii=False, default=str)
