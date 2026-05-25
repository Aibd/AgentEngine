from __future__ import annotations

from examples.services.reporting.models import ReportBlock, ReportData


def render_markdown(report: ReportData) -> str:
    lines: list[str] = [
        f"# {report.meta.title}",
        "",
    ]
    if report.meta.period:
        lines.extend([f"> 报告期间：{report.meta.period}", ""])

    for block in report.sections:
        lines.extend(_render_block(block))

    if report.appendix:
        lines.extend(["", "## 附录", ""])
        for block in report.appendix:
            lines.extend(_render_block(block))

    return "\n".join(lines).strip() + "\n"


def _render_block(block: ReportBlock) -> list[str]:
    if block.type == "heading":
        level = max(1, min(block.level or 2, 6))
        return [f"{'#' * level} {block.text or ''}", ""]
    if block.type == "paragraph":
        return [block.text or "", ""]
    if block.type == "callout":
        return [f"> {block.text or ''}", ""]
    if block.type == "kpi":
        rows = [["指标", "数值", "变化", "趋势"]]
        for item in block.kpis or []:
            rows.append([item.label, item.value, item.delta, item.trend])
        return _render_table(rows)
    if block.type == "table":
        return _render_table([block.headers or [], *(block.rows or [])])
    if block.type == "chart":
        caption = block.text or block.chart.title if block.chart else block.text
        return [f"![{caption or 'chart'}]({block.chart_id or ''})", ""]
    if block.type == "page_break":
        return ["---", ""]
    return []


def _render_table(rows: list[list[object]]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [[_cell(row[i]) if i < len(row) else "" for i in range(width)] for row in rows]
    header = normalized[0]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return lines


def _cell(value: object) -> str:
    return "" if value is None else str(value).replace("|", "\\|")
