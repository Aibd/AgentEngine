"""Tests for ReportAgentTools (P1: on-demand full-content access)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from examples.services.reporting.agent_tools import (
    MAX_SHEET_ROWS,
    MAX_TEXT_CHARS,
    ReportAgentTools,
    TOOL_LIST_UPLOADED,
    TOOL_QUERY_SHEET,
    TOOL_READ_SHEET,
    TOOL_READ_TEXT,
    tool_specs,
)
from examples.services.reporting.file_store import ReportFileStore


pytestmark = pytest.mark.asyncio


def _csv_bytes(rows: list[list[str]]) -> bytes:
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


async def _make_store_and_record(tmp_path: Path, rows: list[list[str]]):
    store = ReportFileStore(tmp_path / "uploads")
    record = await store.save_bytes(
        data=_csv_bytes(rows),
        filename="finance.csv",
        tenant_id="t1",
        conversation_id="c1",
    )
    return store, record


@pytest.mark.asyncio(loop_scope="function")
async def test_tool_specs_cover_all_tools() -> None:
    specs = tool_specs()
    names = {spec["function"]["name"] for spec in specs}
    assert names == {TOOL_LIST_UPLOADED, TOOL_READ_TEXT, TOOL_READ_SHEET, TOOL_QUERY_SHEET}


async def test_list_uploaded_files_returns_only_allowed(tmp_path: Path) -> None:
    store, record = await _make_store_and_record(
        tmp_path,
        [["period", "revenue"], ["2024Q1", "100"], ["2024Q2", "120"]],
    )
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=tmp_path,
    )
    result = await tools.dispatch(TOOL_LIST_UPLOADED, {})
    assert len(result["files"]) == 1
    info = result["files"][0]
    assert info["file_id"] == record.id
    assert info["filename"] == "finance.csv"
    assert info["kind"] == "table"
    assert info["sheets"][0]["row_count"] == 2
    assert info["sheets"][0]["headers"] == ["period", "revenue"]


async def test_read_sheet_returns_full_range(tmp_path: Path) -> None:
    rows = [["period", "revenue"]] + [[f"Q{i}", str(i * 10)] for i in range(1, 8)]
    store, record = await _make_store_and_record(tmp_path, rows)
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=Path(__file__).resolve().parents[1],
    )

    full = await tools.dispatch(TOOL_READ_SHEET, {"file_id": record.id, "row_end": 100})
    assert full["headers"] == ["period", "revenue"]
    assert full["total_rows"] == 7
    assert len(full["rows"]) == 7
    assert full["rows"][-1] == ["Q7", "70"]
    assert full["next_row"] is None


async def test_read_sheet_paginates(tmp_path: Path) -> None:
    rows = [["period", "revenue"]] + [[f"Q{i}", str(i)] for i in range(1, 5)]
    store, record = await _make_store_and_record(tmp_path, rows)
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=Path(__file__).resolve().parents[1],
    )
    first = await tools.dispatch(
        TOOL_READ_SHEET,
        {"file_id": record.id, "row_start": 0, "row_end": 2},
    )
    assert [row[0] for row in first["rows"]] == ["Q1", "Q2"]
    assert first["next_row"] == 2

    second = await tools.dispatch(
        TOOL_READ_SHEET,
        {"file_id": record.id, "row_start": first["next_row"], "row_end": 4},
    )
    assert [row[0] for row in second["rows"]] == ["Q3", "Q4"]
    assert second["next_row"] is None


async def test_query_sheet_aggregates(tmp_path: Path) -> None:
    rows = [["period", "revenue"], ["Q1", "100"], ["Q2", "200"], ["Q3", "300"]]
    store, record = await _make_store_and_record(tmp_path, rows)
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=Path(__file__).resolve().parents[1],
    )
    result = await tools.dispatch(
        TOOL_QUERY_SHEET,
        {"file_id": record.id, "sql": "SELECT SUM(revenue) AS total FROM t"},
    )
    assert "error" not in result, result
    assert result["columns"] == ["total"]
    assert result["rows"][0]["total"] == 600


async def test_query_sheet_blocks_mutations(tmp_path: Path) -> None:
    store, record = await _make_store_and_record(
        tmp_path,
        [["a", "b"], ["1", "2"]],
    )
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=Path(__file__).resolve().parents[1],
    )
    result = await tools.dispatch(
        TOOL_QUERY_SHEET,
        {"file_id": record.id, "sql": "DROP TABLE t"},
    )
    assert "error" in result
    assert "SELECT" in result["error"] or "select" in result["error"].lower()


async def test_unauthorised_file_id_rejected(tmp_path: Path) -> None:
    store, record = await _make_store_and_record(
        tmp_path,
        [["a", "b"], ["1", "2"]],
    )
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[],  # nothing allowed
        project_root=Path(__file__).resolve().parents[1],
    )
    result = await tools.dispatch(
        TOOL_READ_SHEET,
        {"file_id": record.id, "row_end": 1},
    )
    assert "error" in result
    assert "not authorised" in result["error"]


async def test_read_text_uses_full_path_when_preview_truncated(tmp_path: Path) -> None:
    # Build a markdown file longer than PREVIEW_TEXT_CHARS so the parser
    # writes text_full_path.
    from examples.services.reporting.parser import PREVIEW_TEXT_CHARS

    long_content = "段落\n" * (PREVIEW_TEXT_CHARS // 3 + 10)
    store = ReportFileStore(tmp_path / "uploads")
    record = await store.save_bytes(
        data=long_content.encode("utf-8"),
        filename="notes.md",
        tenant_id="t1",
        conversation_id="c1",
    )
    assert record.parsed.text_full_path, "expected full text artifact to be written"
    project_root = Path(__file__).resolve().parents[1]
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=project_root,
    )
    chunk = await tools.dispatch(
        TOOL_READ_TEXT,
        {"file_id": record.id, "offset": 0, "length": 200},
    )
    assert chunk["total_chars"] > len(record.parsed.text_preview)
    assert len(chunk["text"]) == 200
    assert chunk["next_offset"] == 200


async def test_read_text_clamps_length(tmp_path: Path) -> None:
    store = ReportFileStore(tmp_path / "uploads")
    record = await store.save_bytes(
        data=b"hello world\n",
        filename="notes.md",
        tenant_id="t1",
        conversation_id="c1",
    )
    tools = ReportAgentTools(
        store=store,
        allowed_file_ids=[record.id],
        project_root=Path(__file__).resolve().parents[1],
    )
    chunk = await tools.dispatch(
        TOOL_READ_TEXT,
        {"file_id": record.id, "length": MAX_TEXT_CHARS * 10},  # asks too much
    )
    # Clamped to total content (which is much smaller than MAX_TEXT_CHARS)
    assert len(chunk["text"]) <= MAX_TEXT_CHARS
    assert chunk["next_offset"] is None
