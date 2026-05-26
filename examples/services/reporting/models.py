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


class ParsedFile(ReportBaseModel):
    file_id: str
    filename: str
    extension: str
    size_bytes: int
    sheets: list[ParsedSheet] = Field(default_factory=list)
    text_preview: str = ""
    page_count: int = 0
    warnings: list[str] = Field(default_factory=list)
