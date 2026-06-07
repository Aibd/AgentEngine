from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ChartKind = Literal[
    "bar",
    "line",
    "pie",
    "stacked_bar",
    "grouped_bar",
    "area",
    "waterfall",
]
ValueFormat = Literal["currency", "percent", "number"]
class ReportBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartSeries(ReportBaseModel):
    name: str
    data: list[float | int | None]


class ChartSpec(ReportBaseModel):
    kind: ChartKind
    title: str
    x: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    y_format: ValueFormat = "number"
    annotations: list[dict[str, Any]] = Field(default_factory=list)


class KpiItem(ReportBaseModel):
    label: str
    value: str
    delta: str = ""
    trend: Literal["up", "down", "flat"] = "flat"


class ParsedSheet(ReportBaseModel):
    name: str
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    row_count: int = 0
    column_count: int = 0
    # Full-content artifact path (CSV) for this sheet, relative to project root.
    # The ``rows`` field above is a bounded preview (<= 20 rows); the full
    # content lives on disk so agents can stream it on demand.
    full_csv_path: str = ""


class ParsedFile(ReportBaseModel):
    file_id: str
    filename: str
    extension: str
    size_bytes: int
    # ``kind`` lets downstream prompts dispatch without re-sniffing extensions.
    kind: Literal["table", "document", "slides", "image", "text", "pdf", "unknown"] = "unknown"
    sheets: list[ParsedSheet] = Field(default_factory=list)
    # Bounded text snippet safe to embed in every prompt (<= ~6KB).
    text_preview: str = ""
    # Full extracted text path on disk (relative to project root) when the
    # parser produced more text than the preview cap. Empty if the preview
    # already holds everything.
    text_full_path: str = ""
    # Total extracted text length in characters (regardless of where stored).
    text_total_chars: int = 0
    page_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    parser_version: str = "v2"
