from __future__ import annotations

import json
import html
from collections.abc import AsyncIterator

import httpx
import pytest

from agentengine.llm.interfaces import LLMChunk
from app.backend.services import web_api
from app.backend.services.reporting import jobs


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
        midpoint = max(1, len(content) // 2)
        yield LLMChunk(content=content[:midpoint])
        yield LLMChunk(content=content[midpoint:], finish_reason="stop")

    async def close(self) -> None:
        return None


async def _consume_sse(response: httpx.Response) -> AsyncIterator[dict]:
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            lines = raw.splitlines()
            event_name = "message"
            data_lines = []
            for line in lines:
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
            if not data_lines:
                continue
            yield {"event": event_name, "data": json.loads("\n".join(data_lines))}


async def test_report_stream_emits_artifact_sequence_and_snapshot() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        created = await client.post(
            "/api/reports",
            json={
                "conversation_id": "report-test-conv",
                "title": "数据分析 Demo",
                "intent": "stream artifact",
                "skill": "data_analysis",
            },
        )
        assert created.status_code == 200
        assert created.json()["skill"] == "data_analysis"
        report_id = created.json()["id"]

        async with client.stream("GET", f"/api/reports/{report_id}/stream") as response:
            assert response.status_code == 200
            events = [evt async for evt in _consume_sse(response)]

        snapshot = await client.get(f"/api/reports/{report_id}")
        exported = await client.get(f"/api/reports/{report_id}/exports/md")

    event_types = [evt["event"] for evt in events]
    assert event_types[0] == "start"
    assert "step" in event_types
    assert "artifact_start" in event_types
    assert "artifact_html_delta" in event_types
    assert "artifact_ready" in event_types
    assert "artifact_export_ready" in event_types
    assert "step_end" in event_types
    # Companion narration runs after the artifact, so we expect text
    # events emitted by the second LLM turn.
    assert "text" in event_types
    # Fake tool-call events were removed; ensure they don't reappear.
    assert "tool_call_start" not in event_types
    assert "tool_result" not in event_types

    for evt in events:
        assert evt["data"]["artifact_id"] == report_id
        assert evt["data"]["conversation_id"] == "report-test-conv"
        assert evt["data"]["request_id"].startswith("report-")

    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["status"] == "ready"
    assert body["html_length"] > 0
    assert "数据分析 Demo" in body["html"]
    assert body["exports"]["md"].endswith("/exports/md")
    assert body["exports"]["html"].endswith("/exports/html")
    assert body["exports"]["pdf"].endswith("/exports/pdf")
    assert body["exports"]["word"].endswith("/exports/word")

    assert exported.status_code == 200
    assert "数据分析 Demo" in exported.text


async def test_report_export_rejects_unknown_format() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post("/api/reports", json={"title": "Report"})
        response = await client.get(f"/api/reports/{created.json()['id']}/exports/xlsx")

    assert response.status_code == 501


async def test_upload_csv_and_generate_report_from_file() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    csv_bytes = "period,revenue,net_income\n2023Q1,698,151\n2024Q1,805,237\n".encode("utf-8")
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        uploaded = await client.post(
            "/api/report-files",
            params={"filename": "finance.csv", "conversation_id": "upload-test-conv"},
            content=csv_bytes,
            headers={"content-type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200
        file_id = uploaded.json()["id"]

        created = await client.post(
            "/api/reports",
            json={
                "conversation_id": "upload-test-conv",
                "title": "Uploaded Financial Report",
                "file_ids": [file_id],
            },
        )
        report_id = created.json()["id"]

        async with client.stream("GET", f"/api/reports/{report_id}/stream") as response:
            events = [evt async for evt in _consume_sse(response)]

        snapshot = await client.get(f"/api/reports/{report_id}")

    assert uploaded.json()["parsed"]["sheets"][0]["headers"] == ["period", "revenue", "net_income"]
    assert created.status_code == 200
    assert created.json()["file_ids"] == [file_id]
    assert snapshot.status_code == 200
    assert "period" in snapshot.json()["html"]
    assert "revenue" in snapshot.json()["html"]
    assert any(evt["event"] == "artifact_html_delta" for evt in events)
    assert not any(evt["event"] == "artifact_block_added" for evt in events)


async def test_report_uses_previous_conversation_uploads_when_file_ids_omitted() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    csv_bytes = "period,revenue\n2023Q1,698\n2024Q1,805\n".encode("utf-8")
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        uploaded = await client.post(
            "/api/report-files",
            params={"filename": "history.csv", "conversation_id": "history-file-conv"},
            content=csv_bytes,
            headers={"content-type": "application/octet-stream"},
        )
        assert uploaded.status_code == 200

        created = await client.post(
            "/api/reports",
            json={
                "conversation_id": "history-file-conv",
                "title": "History File Report",
                "intent": "use previous uploads",
                "skill": "data_analysis",
            },
        )

    assert created.status_code == 200
    assert created.json()["file_ids"] == [uploaded.json()["id"]]


async def test_upload_pdf_attachment_is_accepted() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/report-files",
            params={"filename": "GOOG-10-Q-Q1-2026.pdf", "conversation_id": "pdf-upload-test"},
            content=b"%PDF-1.4\n%%EOF\n",
            headers={"content-type": "application/octet-stream"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "GOOG-10-Q-Q1-2026.pdf"
    assert body["parsed"]["extension"] == ".pdf"
    assert body["parsed"]["sheets"] == []


async def test_upload_rejects_unsupported_extension() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/report-files",
            params={"filename": "finance.txt"},
            content=b"not supported",
        )

    assert response.status_code == 415
