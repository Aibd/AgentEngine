from __future__ import annotations

import html
from io import BytesIO

from docx import Document
import httpx
import pytest

from agentengine.llm.interfaces import LLMChunk
from app.backend.services import web_api
from app.backend.services.reporting import jobs
from app.backend.services.reporting.pdf_renderer import prepare_print_html


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fake_report_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(jobs, "create_llm_from_env", lambda required=False: _FakeReportLLM())


class _FakeReportLLM:
    async def chat_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        body = html.escape(messages[1].content)
        content = f"<!doctype html><html><head><title>Report</title></head><body><pre>{body}</pre></body></html>"
        yield LLMChunk(content=content[: max(1, len(content) // 2)])
        yield LLMChunk(content=content[max(1, len(content) // 2) :], finish_reason="stop")

    async def close(self) -> None:
        return None


async def _complete_demo_report(client: httpx.AsyncClient, *, title: str = "Export Ready Report") -> str:
    created = await client.post("/api/reports", json={"title": title})
    assert created.status_code == 200
    report_id = created.json()["id"]
    async with client.stream("GET", f"/api/reports/{report_id}/stream") as response:
        assert response.status_code == 200
        async for _ in response.aiter_text():
            pass
    return report_id


async def test_html_export_is_standalone() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        report_id = await _complete_demo_report(client)
        response = await client.get(f"/api/reports/{report_id}/exports/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"] == f'attachment; filename="{report_id}.html"'
    assert "<!doctype html>" in response.text
    assert "Export Ready Report" in response.text


async def test_word_export_returns_native_docx() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        report_id = await _complete_demo_report(client, title="Word Export Report")
        response = await client.get(f"/api/reports/{report_id}/exports/word")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.headers["content-disposition"] == f'attachment; filename="{report_id}.docx"'
    assert response.content.startswith(b"PK")
    document = Document(BytesIO(response.content))
    assert "Word Export Report" in "\n".join(paragraph.text for paragraph in document.paragraphs)


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


async def test_pdf_print_html_injects_pagination_css() -> None:
    prepared = prepare_print_html("<!doctype html><html><head><title>x</title></head><body><section>Body</section></body></html>")

    assert "agentengine-export-print-css" in prepared
    assert "@page" in prepared
    assert "break-inside: avoid" in prepared
    assert "table-header-group" in prepared


async def test_pdf_export_reports_missing_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_: str) -> bytes:
        raise web_api.PdfRendererUnavailable("missing browser")

    monkeypatch.setattr(web_api, "render_pdf", unavailable)
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        report_id = await _complete_demo_report(client, title="Report")
        response = await client.get(f"/api/reports/{report_id}/exports/pdf")

    assert response.status_code == 501
    assert response.json()["detail"] == "missing browser"


async def test_export_before_html_is_ready_returns_conflict() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        created = await client.post("/api/reports", json={"title": "Report"})
        response = await client.get(f"/api/reports/{created.json()['id']}/exports/html")

    assert response.status_code == 409
