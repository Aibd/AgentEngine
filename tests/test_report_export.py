from __future__ import annotations

import zipfile
from io import BytesIO

import httpx
import pytest

from examples.services import web_api


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _disable_report_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


async def _complete_demo_report(client: httpx.AsyncClient, *, title: str = "Export Ready Report") -> str:
    created = await client.post("/api/reports", json={"title": title})
    assert created.status_code == 200
    report_id = created.json()["id"]
    async with client.stream("GET", f"/api/reports/{report_id}/stream") as response:
        assert response.status_code == 200
        async for _ in response.aiter_text():
            pass
    return report_id


async def test_html_export_is_standalone_and_inlines_chart_svg() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        report_id = await _complete_demo_report(client)
        response = await client.get(f"/api/reports/{report_id}/exports/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"] == f'attachment; filename="{report_id}.html"'
    assert "<!doctype html>" in response.text
    assert "@page" in response.text
    assert "<svg" in response.text
    assert "Export Ready Report" in response.text


async def test_docx_export_is_valid_openxml_package() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        report_id = await _complete_demo_report(client, title="Word Export Report")
        response = await client.get(f"/api/reports/{report_id}/exports/docx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.headers["content-disposition"] == f'attachment; filename="{report_id}.docx"'
    with zipfile.ZipFile(BytesIO(response.content)) as package:
        names = set(package.namelist())
        document = package.read("word/document.xml").decode("utf-8")
        rels = package.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "[Content_Types].xml" in names
    assert "word/styles.xml" in names
    assert "word/media/chart_1.svg" in names
    assert "Word Export Report" in document
    assert "image/svg+xml" in package_content_types(response.content)
    assert "media/chart_1.svg" in rels


async def test_pdf_export_returns_browser_pdf_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_render_pdf(html: str) -> bytes:
        seen["html"] = html
        return b"%PDF-1.4\n%%EOF\n"

    monkeypatch.setattr(web_api, "render_pdf", fake_render_pdf)
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        report_id = await _complete_demo_report(client, title="PDF Export Report")
        response = await client.get(f"/api/reports/{report_id}/exports/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="{report_id}.pdf"'
    assert response.content.startswith(b"%PDF")
    assert "PDF Export Report" in seen["html"]


async def test_pdf_export_reports_missing_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_: str) -> bytes:
        raise web_api.PdfRendererUnavailable("missing browser")

    monkeypatch.setattr(web_api, "render_pdf", unavailable)
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        created = await client.post("/api/reports", json={"title": "Report"})
        response = await client.get(f"/api/reports/{created.json()['id']}/exports/pdf")

    assert response.status_code == 501
    assert response.json()["detail"] == "missing browser"


def package_content_types(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as package:
        return package.read("[Content_Types].xml").decode("utf-8")
