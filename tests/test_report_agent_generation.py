from __future__ import annotations

import asyncio

import pytest

from agentengine.llm.interfaces import LLMChunk
from examples.services.reporting import jobs
from examples.services.reporting.jobs import ReportJobStore, extract_model_html, stream_report_artifact


def test_extract_model_html_accepts_fenced_html_and_strips_unsafe_script() -> None:
    """ECharts inline scripts are kept; remote scripts not from trusted CDNs
    and inline event handlers are stripped."""

    content = """
    ```html
    <!doctype html>
    <html><head>
    <title>Report</title>
    <script src=\"https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js\"></script>
    <script src=\"https://evil.example.com/exfil.js\"></script>
    </head><body>
    <h1 onclick=\"alert(1)\">Report</h1>
    <a href=\"javascript:steal()\">click</a>
    <div id=\"chart_1\"></div>
    <script>echarts.init(document.getElementById('chart_1')).setOption({});</script>
    </body></html>
    ```
    """

    output = extract_model_html(content, title="Report")
    lowered = output.lower()

    # Heading text survives (event handler stripped, text intact)
    assert "<h1" in lowered and "report</h1>" in lowered
    assert "onclick" not in lowered
    # javascript: URL is stripped
    assert "javascript:" not in lowered
    # Trusted CDN script is preserved
    assert "cdn.jsdelivr.net/npm/echarts" in lowered
    # Untrusted CDN script is removed
    assert "evil.example.com" not in lowered
    # Inline ECharts init script is preserved
    assert "echarts.init" in lowered


def test_extract_model_html_wraps_body_fragment() -> None:
    output = extract_model_html("<section><h1>Fragment</h1></section>", title="Wrapped")

    assert "<!doctype html>" in output
    assert "<title>Wrapped</title>" in output
    assert "<section><h1>Fragment</h1></section>" in output


def test_report_job_store_preserves_selected_skill() -> None:
    store = ReportJobStore()

    job = store.create(
        conversation_id="conv",
        title="Report",
        intent="analyze uploaded files",
        skill="data_analysis",
    )

    assert job.skill == "data_analysis"
    assert job.snapshot()["skill"] == "data_analysis"


@pytest.mark.asyncio
async def test_report_stream_uses_llm_html_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLLM:
        async def chat_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            assert "上传文件上下文" in messages[1].content
            yield LLMChunk(content="<!doctype html><html><head><title>Custom</title></head><body>")
            yield LLMChunk(content="<h1>Streamed HTML Report</h1></body></html>", finish_reason="stop")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(jobs, "create_llm_from_env", lambda required=False: FakeLLM())
    store = ReportJobStore()
    job = store.create(
        conversation_id="conv",
        title="Custom",
        intent="generate a custom report",
        skill="data_analysis",
    )

    events = [event async for event in stream_report_artifact(job=job, request_id="report-test")]

    html_events = [event for event in events if event["event"] == "artifact_html_delta"]
    assert len(html_events) >= 2
    assert "Streamed HTML Report" in "".join(event["data"]["delta"] for event in html_events)
    assert any(event["data"].get("replace") is True for event in html_events) is False
    assert not any(event["event"] == "artifact_block_added" for event in events)


@pytest.mark.asyncio
async def test_report_stream_normalizes_streamed_markdown_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLLM:
        async def chat_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            yield LLMChunk(content="```html\n<!doctype html><html><body>")
            yield LLMChunk(content="<h1>Fenced</h1></body></html>\n```", finish_reason="stop")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(jobs, "create_llm_from_env", lambda required=False: FakeLLM())
    job = ReportJobStore().create(conversation_id="conv", title="Fenced", intent="generate", skill="data_analysis")

    events = [event async for event in stream_report_artifact(job=job, request_id="report-test")]

    replacements = [
        event for event in events
        if event["event"] == "artifact_html_delta" and event["data"].get("replace") is True
    ]
    assert replacements
    assert job.html.startswith("<!doctype html>")


@pytest.mark.asyncio
async def test_report_stream_replays_after_client_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowLLM:
        async def chat_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            yield LLMChunk(content="<!doctype html><html><body>")
            await asyncio.sleep(0.01)
            yield LLMChunk(content="<h1>Completed after disconnect</h1></body></html>", finish_reason="stop")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(jobs, "create_llm_from_env", lambda required=False: SlowLLM())
    job = ReportJobStore().create(conversation_id="conv", title="Reconnect", intent="generate", skill="data_analysis")

    first_stream = stream_report_artifact(job=job, request_id="first-request")
    async for event in first_stream:
        if event["event"] == "artifact_html_delta":
            break
    await first_stream.aclose()

    assert job.generation_task is not None
    await asyncio.wait_for(job.generation_task, timeout=1)
    replayed = [event async for event in stream_report_artifact(job=job, request_id="reconnect-request")]

    assert job.status == "ready"
    assert replayed[0]["event"] == "start"
    assert all(event["data"]["request_id"] == "reconnect-request" for event in replayed)
    assert "Completed after disconnect" in "".join(
        event["data"].get("delta", "") for event in replayed if event["event"] == "artifact_html_delta"
    )


@pytest.mark.asyncio
async def test_report_stream_fails_when_llm_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "create_llm_from_env", lambda required=False: None)
    store = ReportJobStore()
    job = store.create(conversation_id="conv", title="Missing LLM", intent="generate", skill="data_analysis")

    events = [event async for event in stream_report_artifact(job=job, request_id="report-test")]

    assert job.status == "failed"
    assert any(event["event"] == "artifact_error" for event in events)
    assert not any(event["event"] == "artifact_html_delta" for event in events)
