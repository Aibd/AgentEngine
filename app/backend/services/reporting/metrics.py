from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.backend.services.reporting.models import ChartSeries, ChartSpec, KpiItem


@dataclass(slots=True)
class MetricSeries:
    code: str
    label: str
    periods: list[str]
    values: list[float]


@dataclass(slots=True)
class FinancialMetrics:
    kpis: list[KpiItem] = field(default_factory=list)
    series: list[MetricSeries] = field(default_factory=list)
    chart: ChartSpec | None = None
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_COLUMN_KEYWORDS = {
    "period": ["period", "date", "quarter", "year", "期间", "报告期", "季度", "年份", "月份", "时间"],
    "revenue": ["revenue", "sales", "营业收入", "收入", "营收", "销售额"],
    "net_income": ["net_income", "net income", "profit", "净利润", "归母净利润", "利润"],
    "operating_income": ["operating_income", "operating income", "营业利润", "经营利润"],
    "cash_flow": ["cash flow", "operating cash", "经营现金流", "经营活动现金流", "现金流"],
}

_LABELS = {
    "revenue": "营业收入",
    "net_income": "净利润",
    "operating_income": "营业利润",
    "cash_flow": "经营现金流",
}


def analyze_first_sheet(file_briefs: list[dict[str, Any]]) -> FinancialMetrics:
    if not file_briefs:
        return FinancialMetrics(warnings=["no uploaded files"])
    first_file = file_briefs[0]
    sheets = first_file.get("sheets") or []
    if not sheets:
        return FinancialMetrics(warnings=["uploaded file has no readable sheets"])
    sheet = sheets[0]
    headers = [str(item) for item in sheet.get("headers", [])]
    rows = [row for row in sheet.get("rows", []) if isinstance(row, list)]
    if not headers or not rows:
        return FinancialMetrics(warnings=["first sheet has no preview rows"])

    indices = _infer_columns(headers)
    period_index = indices.get("period")
    periods = _periods(rows, period_index)
    metric_series: list[MetricSeries] = []
    for code in ("revenue", "net_income", "operating_income", "cash_flow"):
        index = indices.get(code)
        if index is None:
            continue
        values = [_parse_number(row[index] if index < len(row) else "") for row in rows]
        pairs = [(period, value) for period, value in zip(periods, values, strict=False) if value is not None]
        if not pairs:
            continue
        metric_series.append(
            MetricSeries(
                code=code,
                label=_LABELS[code],
                periods=[period for period, _ in pairs],
                values=[value for _, value in pairs],
            )
        )

    kpis = [_kpi_for_series(series) for series in metric_series[:3]]
    findings = _findings(metric_series)
    chart = _chart(metric_series)
    warnings = []
    if not metric_series:
        warnings.append("no known financial metric columns were detected")
    if "revenue" not in {series.code for series in metric_series}:
        warnings.append("revenue column was not detected")
    return FinancialMetrics(kpis=kpis, series=metric_series, chart=chart, findings=findings, warnings=warnings)


def _infer_columns(headers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    normalized = [_normalize_header(header) for header in headers]
    for code, keywords in _COLUMN_KEYWORDS.items():
        for index, header in enumerate(normalized):
            if any(keyword.lower() in header for keyword in keywords):
                out[code] = index
                break
    return out


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _periods(rows: list[list[Any]], period_index: int | None) -> list[str]:
    if period_index is None:
        return [f"第 {index + 1} 期" for index in range(len(rows))]
    return [
        str(row[period_index]).strip() if period_index < len(row) and str(row[period_index]).strip() else f"第 {index + 1} 期"
        for index, row in enumerate(rows)
    ]


def _parse_number(value: Any) -> float | None:
    raw = str(value).strip()
    if not raw or raw in {"-", "--", "N/A", "na", "null"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    multiplier = 1.0
    if "亿" in raw:
        multiplier = 100_000_000.0
    elif "万" in raw:
        multiplier = 10_000.0
    percent = "%" in raw
    cleaned = re.sub(r"[^0-9.+-]", "", raw.replace(",", ""))
    if cleaned in {"", ".", "+", "-"}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if negative:
        number = -number
    if percent:
        number = number / 100.0
    return number * multiplier


def _kpi_for_series(series: MetricSeries) -> KpiItem:
    latest = series.values[-1]
    previous = series.values[-2] if len(series.values) >= 2 else None
    delta = ""
    trend = "flat"
    if previous not in (None, 0):
        change = (latest - previous) / abs(previous)
        delta = _format_percent(change)
        if change > 0:
            trend = "up"
        elif change < 0:
            trend = "down"
    return KpiItem(
        label=series.label,
        value=_format_amount(latest),
        delta=delta,
        trend=trend,
    )


def _findings(series_list: list[MetricSeries]) -> list[str]:
    findings: list[str] = []
    by_code = {series.code: series for series in series_list}
    revenue = by_code.get("revenue")
    net_income = by_code.get("net_income")
    if revenue and len(revenue.values) >= 2:
        change = _safe_change(revenue.values[-1], revenue.values[-2])
        if change is not None:
            findings.append(f"最近一期营业收入为 {_format_amount(revenue.values[-1])}，较上一期变化 {_format_percent(change)}。")
    if revenue and net_income and revenue.values[-1]:
        margin = net_income.values[-1] / revenue.values[-1]
        findings.append(f"最近一期净利率约为 {_format_percent(margin)}，可作为盈利质量的核心观察指标。")
    return findings


def _chart(series_list: list[MetricSeries]) -> ChartSpec | None:
    visible = [series for series in series_list if series.code in {"revenue", "net_income", "operating_income"}]
    if not visible:
        return None
    periods = visible[0].periods
    return ChartSpec(
        kind="line",
        title="核心财务指标趋势",
        x=periods,
        y_format="currency",
        series=[
            ChartSeries(name=series.label, data=[_display_unit(value) for value in series.values])
            for series in visible[:3]
        ],
    )


def _safe_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / abs(previous)


def _format_amount(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿"
    if abs_value >= 10_000:
        return f"{value / 10_000:.2f} 万"
    if math.isclose(value, round(value)):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _format_percent(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _display_unit(value: float) -> float:
    if abs(value) >= 100_000_000:
        return round(value / 100_000_000, 2)
    if abs(value) >= 10_000:
        return round(value / 10_000, 2)
    return round(value, 2)
