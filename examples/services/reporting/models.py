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
BlockType = Literal[
    "heading",
    "paragraph",
    "table",
    "chart",
    "kpi",
    "callout",
    "page_break",
]


class ReportBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportMeta(ReportBaseModel):
    title: str
    author: str = "AgentEngine"
    period: str = ""
    currency: str = "CNY"
    locale: str = "zh-CN"


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


class ReportBlock(ReportBaseModel):
    type: BlockType
    level: int | None = None
    text: str | None = None
    headers: list[str] | None = None
    rows: list[list[str | int | float | None]] | None = None
    chart_id: str | None = None
    chart: ChartSpec | None = None
    kpis: list[KpiItem] | None = None
    style: dict[str, Any] = Field(default_factory=dict)


class ReportData(ReportBaseModel):
    id: str
    meta: ReportMeta
    cover: dict[str, Any] = Field(default_factory=dict)
    toc: bool = True
    sections: list[ReportBlock] = Field(default_factory=list)
    appendix: list[ReportBlock] = Field(default_factory=list)

    def append_block(self, block: ReportBlock) -> None:
        self.sections.append(block)


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


def empty_report(report_id: str, *, title: str, period: str = "") -> ReportData:
    return ReportData(
        id=report_id,
        meta=ReportMeta(title=title, period=period),
        cover={"title": title, "period": period},
        toc=True,
    )
