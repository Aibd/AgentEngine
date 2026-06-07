from __future__ import annotations

from pathlib import Path

import pytest

from app.backend.services.reporting.parser import parse_attachment_metadata, parse_financial_file


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
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["period", "revenue"])
    sheet.append(["2023Q1", 698])
    sheet.append(["2024Q1", 805])
    workbook.save(path)
