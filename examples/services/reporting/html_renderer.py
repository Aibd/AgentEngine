from __future__ import annotations

from html import escape

from examples.services.reporting.charts import render_svg
from examples.services.reporting.models import ReportBlock, ReportData


def render_html(report: ReportData, *, chart_assets: dict[str, str] | None = None) -> str:
    assets = chart_assets or {}
    title = escape(report.meta.title)
    period = escape(report.meta.period)
    blocks = "\n".join(_render_block(block, assets) for block in report.sections)
    appendix = "\n".join(_render_block(block, assets) for block in report.appendix)
    appendix_html = f"<section class=\"appendix\"><h2>Appendix</h2>{appendix}</section>" if appendix else ""
    period_html = f"<p class=\"report-period\">{period}</p>" if period else ""

    return f"""<!doctype html>
<html lang="{escape(report.meta.locale or 'zh-CN')}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    @page {{ size: A4; margin: 18mm 16mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #f3f5f8;
      color: #111827;
      font-family: "Source Han Sans SC", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      line-height: 1.68;
    }}
    .report-shell {{ padding: 28px 0; }}
    .report-paper {{
      width: min(820px, calc(100vw - 32px));
      min-height: 1120px;
      margin: 0 auto;
      padding: 56px 64px;
      background: #fff;
      box-shadow: 0 12px 40px rgba(15, 23, 42, .12);
    }}
    .cover {{ border-bottom: 4px solid #2563eb; margin-bottom: 30px; padding-bottom: 24px; }}
    .cover h1 {{ margin: 0; font-size: 32px; line-height: 1.22; color: #0f172a; }}
    .report-period {{ margin: 12px 0 0; color: #64748b; font-size: 15px; }}
    h1, h2, h3 {{ color: #0f172a; break-after: avoid; line-height: 1.3; }}
    h1 {{ font-size: 30px; margin: 30px 0 14px; }}
    h2 {{ font-size: 22px; margin: 28px 0 12px; border-left: 4px solid #2563eb; padding-left: 12px; }}
    h3 {{ font-size: 18px; margin: 22px 0 10px; }}
    p {{ margin: 0 0 14px; font-size: 14px; }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0 24px;
    }}
    .kpi {{
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 16px;
      background: #fbfdff;
      break-inside: avoid;
    }}
    .kpi span {{ display: block; color: #64748b; font-size: 12px; }}
    .kpi strong {{ display: block; margin-top: 6px; font-size: 24px; color: #0f172a; }}
    .kpi em {{ display: block; margin-top: 6px; font-size: 12px; font-style: normal; }}
    .trend-up {{ color: #059669; }}
    .trend-down {{ color: #dc2626; }}
    .trend-flat {{ color: #64748b; }}
    .table-wrap {{ overflow: hidden; border: 1px solid #e5e7eb; border-radius: 8px; margin: 16px 0 24px; break-inside: avoid; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{ background: #f1f5f9; color: #334155; text-align: left; font-weight: 700; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 9px 10px; vertical-align: top; }}
    tr:nth-child(even) td {{ background: #fafafa; }}
    tr:last-child td {{ border-bottom: 0; }}
    figure {{ margin: 18px 0 26px; break-inside: avoid; }}
    figure svg {{ width: 100%; height: auto; display: block; border: 1px solid #e5e7eb; border-radius: 8px; }}
    figcaption {{ margin-top: 8px; color: #64748b; font-size: 12px; text-align: center; }}
    .callout {{
      margin: 18px 0 24px;
      padding: 14px 16px;
      border-left: 4px solid #0ea5e9;
      background: #f0f9ff;
      color: #0f172a;
      break-inside: avoid;
    }}
    .page-break {{ break-after: page; border: 0; margin: 0; }}
    .appendix {{ margin-top: 34px; padding-top: 22px; border-top: 1px solid #e5e7eb; }}
    @media print {{
      body {{ background: #fff; }}
      .report-shell {{ padding: 0; }}
      .report-paper {{ width: auto; min-height: auto; box-shadow: none; padding: 0; }}
      figure svg, .table-wrap, .kpi {{ break-inside: avoid; }}
    }}
    @media (max-width: 720px) {{
      .report-paper {{ padding: 32px 24px; }}
      .kpi-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="report-shell">
    <article class="report-paper">
      <header class="cover">
        <h1>{title}</h1>
        {period_html}
      </header>
      {blocks}
      {appendix_html}
    </article>
  </main>
</body>
</html>
"""


def _render_block(block: ReportBlock, chart_assets: dict[str, str]) -> str:
    if block.type == "heading":
        level = max(1, min(block.level or 2, 3))
        return f"<h{level}>{escape(block.text or '')}</h{level}>"
    if block.type == "paragraph":
        return f"<p>{escape(block.text or '')}</p>"
    if block.type == "callout":
        return f"<aside class=\"callout\">{escape(block.text or '')}</aside>"
    if block.type == "kpi":
        items = "".join(
            (
                "<article class=\"kpi\">"
                f"<span>{escape(item.label)}</span>"
                f"<strong>{escape(item.value)}</strong>"
                f"<em class=\"trend-{escape(item.trend)}\">{escape(item.delta)}</em>"
                "</article>"
            )
            for item in (block.kpis or [])
        )
        return f"<section class=\"kpi-grid\">{items}</section>"
    if block.type == "table":
        return _render_table(block.headers or [], block.rows or [])
    if block.type == "chart":
        svg = ""
        if block.chart_id and block.chart_id in chart_assets:
            svg = chart_assets[block.chart_id]
        elif block.chart is not None:
            svg = render_svg(block.chart)
        caption = escape(block.text or (block.chart.title if block.chart else ""))
        return f"<figure>{svg}<figcaption>{caption}</figcaption></figure>"
    if block.type == "page_break":
        return "<hr class=\"page-break\">"
    return ""


def _render_table(headers: list[str], rows: list[list[str | int | float | None]]) -> str:
    head = ""
    if headers:
        header_cells = "".join(f"<th>{escape(str(header))}</th>" for header in headers)
        head = f"<thead><tr>{header_cells}</tr></thead>"
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape('' if cell is None else str(cell))}</td>" for cell in row)
        body_rows.append(f"<tr>{cells}</tr>")
    body = f"<tbody>{''.join(body_rows)}</tbody>"
    return f"<div class=\"table-wrap\"><table>{head}{body}</table></div>"
