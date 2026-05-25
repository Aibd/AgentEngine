from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape

from examples.services.reporting.charts import render_svg
from examples.services.reporting.models import ChartSpec, ReportBlock, ReportData


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def render_docx(report: ReportData, *, chart_assets: dict[str, str] | None = None) -> bytes:
    builder = _DocxBuilder(chart_assets=chart_assets or {})
    body = [
        _paragraph(report.meta.title, style="Title"),
        *([_paragraph(report.meta.period, style="Subtitle")] if report.meta.period else []),
    ]
    for block in report.sections:
        body.extend(builder.render_block(block))
    if report.appendix:
        body.append(_paragraph("Appendix", style="Heading2"))
        for block in report.appendix:
            body.extend(builder.render_block(block))
    document = _document_xml("".join(body))
    return builder.package(report=report, document_xml=document)


@dataclass(slots=True)
class _DocxBuilder:
    chart_assets: dict[str, str]
    image_rels: list[tuple[str, str]] = field(default_factory=list)
    media: dict[str, str] = field(default_factory=dict)

    def render_block(self, block: ReportBlock) -> list[str]:
        if block.type == "heading":
            level = max(1, min(block.level or 2, 3))
            return [_paragraph(block.text or "", style=f"Heading{level}")]
        if block.type == "paragraph":
            return [_paragraph(block.text or "")]
        if block.type == "callout":
            return [_paragraph(block.text or "", style="Quote")]
        if block.type == "kpi":
            rows = [["Metric", "Value", "Delta", "Trend"]]
            for item in block.kpis or []:
                rows.append([item.label, item.value, item.delta, item.trend])
            return [_table(rows, header=True)]
        if block.type == "table":
            return [_table([block.headers or [], *(block.rows or [])], header=bool(block.headers))]
        if block.type == "chart":
            return self._chart(block)
        if block.type == "page_break":
            return [_paragraph("", page_break=True)]
        return []

    def _chart(self, block: ReportBlock) -> list[str]:
        svg = ""
        if block.chart_id and block.chart_id in self.chart_assets:
            svg = self.chart_assets[block.chart_id]
        elif block.chart is not None:
            svg = render_svg(block.chart)
        caption = block.text or (block.chart.title if block.chart else "Chart")
        output = [_paragraph(caption, style="Caption")]
        if svg:
            rel_id = f"rId{len(self.image_rels) + 1}"
            media_name = f"media/chart_{len(self.image_rels) + 1}.svg"
            self.image_rels.append((rel_id, media_name))
            self.media[media_name] = svg
            output.append(_svg_drawing(rel_id, caption))
        if block.chart is not None:
            output.append(_chart_data_table(block.chart))
        return output

    def package(self, *, report: ReportData, document_xml: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", _content_types(has_svg=bool(self.media)))
            package.writestr("_rels/.rels", _root_rels())
            package.writestr("docProps/core.xml", _core_props(report.meta.title))
            package.writestr("docProps/app.xml", _app_props())
            package.writestr("word/document.xml", document_xml)
            package.writestr("word/styles.xml", _styles_xml())
            package.writestr("word/settings.xml", _settings_xml())
            package.writestr("word/_rels/document.xml.rels", _document_rels(self.image_rels))
            for name, content in self.media.items():
                package.writestr(f"word/{name}", content.encode("utf-8"))
        return buffer.getvalue()


def _document_xml(body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
  xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="992" w:bottom="1134" w:left="992" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>"""


def _paragraph(text: str, *, style: str = "Normal", page_break: bool = False) -> str:
    break_xml = "<w:br w:type=\"page\"/>" if page_break else ""
    text_xml = f"<w:t xml:space=\"preserve\">{_xml_text(text)}</w:t>" if text else ""
    return f"""<w:p>
  <w:pPr><w:pStyle w:val="{style}"/></w:pPr>
  <w:r>{break_xml}{text_xml}</w:r>
</w:p>"""


def _table(rows: list[list[object]], *, header: bool) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [[row[index] if index < len(row) else "" for index in range(width)] for row in rows]
    table_rows = []
    for row_index, row in enumerate(normalized):
        cells = []
        for cell in row:
            shade = '<w:shd w:fill="EAF1F8"/>' if header and row_index == 0 else ""
            cells.append(
                f"""<w:tc>
  <w:tcPr><w:tcW w:w="{max(1200, 9000 // width)}" w:type="dxa"/>{shade}</w:tcPr>
  {_paragraph("" if cell is None else str(cell))}
</w:tc>"""
            )
        table_rows.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return f"""<w:tbl>
  <w:tblPr>
    <w:tblW w:w="0" w:type="auto"/>
    <w:tblBorders>
      <w:top w:val="single" w:sz="4" w:color="D9E2EC"/>
      <w:left w:val="single" w:sz="4" w:color="D9E2EC"/>
      <w:bottom w:val="single" w:sz="4" w:color="D9E2EC"/>
      <w:right w:val="single" w:sz="4" w:color="D9E2EC"/>
      <w:insideH w:val="single" w:sz="4" w:color="D9E2EC"/>
      <w:insideV w:val="single" w:sz="4" w:color="D9E2EC"/>
    </w:tblBorders>
  </w:tblPr>
  {''.join(table_rows)}
</w:tbl>"""


def _chart_data_table(chart: ChartSpec) -> str:
    rows: list[list[object]] = [["Period", *[series.name for series in chart.series]]]
    for index, label in enumerate(chart.x):
        rows.append([label, *[_series_value(series.data, index) for series in chart.series]])
    return _table(rows, header=True)


def _series_value(values: list[float | int | None], index: int) -> str:
    if index >= len(values) or values[index] is None:
        return ""
    return str(values[index])


def _svg_drawing(rel_id: str, alt_text: str) -> str:
    cx = 5486400
    cy = 3291840
    return f"""<w:p>
  <w:pPr><w:jc w:val="center"/></w:pPr>
  <w:r>
    <w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="{cx}" cy="{cy}"/>
        <wp:docPr id="1" name="Chart" descr="{_xml_attr(alt_text)}"/>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic>
              <pic:nvPicPr>
                <pic:cNvPr id="0" name="Chart"/>
                <pic:cNvPicPr/>
              </pic:nvPicPr>
              <pic:blipFill>
                <a:blip r:embed="{rel_id}"/>
                <a:stretch><a:fillRect/></a:stretch>
              </pic:blipFill>
              <pic:spPr>
                <a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
              </pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>"""


def _content_types(*, has_svg: bool) -> str:
    svg_type = '<Default Extension="svg" ContentType="image/svg+xml"/>' if has_svg else ""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  {svg_type}
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""


def _root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _document_rels(image_rels: list[tuple[str, str]]) -> str:
    relationships = [
        '<Relationship Id="rStyle" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
        '<Relationship Id="rSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>',
    ]
    relationships.extend(
        f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>'
        for rel_id, target in image_rels
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {''.join(relationships)}
</Relationships>"""


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="280"/></w:pPr><w:rPr><w:b/><w:color w:val="0F172A"/><w:sz w:val="44"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="64748B"/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="360" w:after="160"/></w:pPr><w:rPr><w:b/><w:color w:val="0F172A"/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="300" w:after="140"/></w:pPr><w:rPr><w:b/><w:color w:val="1D4ED8"/><w:sz w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="Heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:b/><w:color w:val="334155"/><w:sz w:val="26"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="240"/><w:spacing w:before="120" w:after="120"/></w:pPr><w:rPr><w:color w:val="0F172A"/><w:i/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="Caption"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="160" w:after="80"/></w:pPr><w:rPr><w:color w:val="64748B"/><w:sz w:val="20"/></w:rPr></w:style>
</w:styles>"""


def _settings_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:zoom w:percent="100"/>
</w:settings>"""


def _core_props(title: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{_xml_text(title)}</dc:title>
  <dc:creator>AgentEngine</dc:creator>
  <cp:lastModifiedBy>AgentEngine</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""


def _app_props() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>AgentEngine</Application>
</Properties>"""


def _xml_text(value: str) -> str:
    return escape(value, quote=False)


def _xml_attr(value: str) -> str:
    return escape(value, quote=True)
