from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from examples.services.reporting.parser import parse_attachment_metadata, parse_financial_file


def test_parse_csv_preview(tmp_path: Path) -> None:
    path = tmp_path / "finance.csv"
    path.write_text("period,revenue,net_income\n2023Q1,698,151\n2024Q1,805,237\n", encoding="utf-8")

    parsed = parse_financial_file(
        file_id="file_csv",
        filename="finance.csv",
        path=path,
        size_bytes=path.stat().st_size,
    )

    assert parsed.extension == ".csv"
    assert parsed.sheets[0].headers == ["period", "revenue", "net_income"]
    assert parsed.sheets[0].rows == [["2023Q1", "698", "151"], ["2024Q1", "805", "237"]]


def test_parse_minimal_xlsx_preview(tmp_path: Path) -> None:
    path = tmp_path / "finance.xlsx"
    _write_minimal_xlsx(path)

    parsed = parse_financial_file(
        file_id="file_xlsx",
        filename="finance.xlsx",
        path=path,
        size_bytes=path.stat().st_size,
    )

    assert parsed.extension == ".xlsx"
    assert parsed.sheets[0].name == "Sheet1"
    assert parsed.sheets[0].headers == ["period", "revenue"]
    assert parsed.sheets[0].rows == [["2023Q1", "698"], ["2024Q1", "805"]]


def test_parse_pdf_text_preview(tmp_path: Path) -> None:
    plt = pytest.importorskip("matplotlib.pyplot")
    backend_pdf = pytest.importorskip("matplotlib.backends.backend_pdf")
    path = tmp_path / "report.pdf"
    with backend_pdf.PdfPages(path) as pdf:
        figure = plt.figure(figsize=(6, 4))
        figure.text(0.1, 0.6, "Revenue 100 Net income 20", fontsize=12)
        pdf.savefig(figure)
        plt.close(figure)

    parsed = parse_attachment_metadata(
        file_id="file_pdf",
        filename="report.pdf",
        extension=".pdf",
        path=path,
        size_bytes=path.stat().st_size,
    )

    assert parsed.extension == ".pdf"
    assert parsed.page_count == 1
    assert "Revenue 100" in parsed.text_preview


def _write_minimal_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>period</t></is></c><c r="B1" t="inlineStr"><is><t>revenue</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>2023Q1</t></is></c><c r="B2"><v>698</v></c></row>
    <row r="3"><c r="A3" t="inlineStr"><is><t>2024Q1</t></is></c><c r="B3"><v>805</v></c></row>
  </sheetData>
</worksheet>""",
        )
