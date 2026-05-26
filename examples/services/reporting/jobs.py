from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentengine.llm.env import create_llm_from_env
from agentengine.memory.message import Message
from examples.services.reporting.file_store import ReportFileRecord


logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ReportJob:
    id: str
    conversation_id: str
    title: str
    intent: str
    skill: str = "data_analysis"
    file_ids: list[str] = field(default_factory=list)
    file_briefs: list[dict[str, Any]] = field(default_factory=list)
    html: str = ""
    chart_assets: dict[str, str] = field(default_factory=dict)
    status: str = "created"
    exports: dict[str, str] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_utc_iso)
    finished_at: str = ""
    events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    event_signal: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    generation_task: asyncio.Task[None] | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "title": self.title,
            "intent": self.intent,
            "skill": self.skill,
            "file_ids": self.file_ids,
            "file_briefs": self.file_briefs,
            "chart_ids": sorted(self.chart_assets),
            "status": self.status,
            "html": self.html,
            "html_length": len(self.html),
            "exports": self.exports,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class ReportJobStore:
    """Process-local report store for the demo service."""

    def __init__(self) -> None:
        self._jobs: dict[str, ReportJob] = {}

    def create(
        self,
        *,
        conversation_id: str,
        title: str,
        intent: str,
        skill: str = "data_analysis",
        files: list[ReportFileRecord] | None = None,
    ) -> ReportJob:
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        file_records = files or []
        job = ReportJob(
            id=report_id,
            conversation_id=conversation_id,
            title=title,
            intent=intent,
            skill=skill,
            file_ids=[record.id for record in file_records],
            file_briefs=[_file_brief(record) for record in file_records],
        )
        self._jobs[report_id] = job
        return job

    def get(self, report_id: str) -> ReportJob | None:
        return self._jobs.get(report_id)


async def stream_report_artifact(
    *,
    job: ReportJob,
    request_id: str,
) -> AsyncIterator[dict[str, Any]]:
    _ensure_report_generation_task(job, request_id=request_id)
    event_index = 0

    while True:
        while event_index < len(job.events):
            yield _with_request_id(job.events[event_index], request_id)
            event_index += 1

        if job.status in {"ready", "failed"}:
            return
        if job.generation_task is not None and job.generation_task.done():
            return

        job.event_signal.clear()
        await job.event_signal.wait()


def _ensure_report_generation_task(job: ReportJob, *, request_id: str) -> None:
    if job.status in {"ready", "failed"}:
        return
    if job.generation_task is not None and not job.generation_task.done():
        return
    job.generation_task = asyncio.create_task(_collect_report_events(job=job, request_id=request_id))


async def _collect_report_events(*, job: ReportJob, request_id: str) -> None:
    try:
        async for frame in _generate_report_artifact_frames(job=job, request_id=request_id):
            job.events.append(frame)
            job.event_signal.set()
    except Exception as exc:
        logger.exception("report_background_generation_failed report_id=%s", job.id)
        job.status = "failed"
        job.error = f"HTML report generation failed: {exc}"
        job.finished_at = _utc_iso()
        job.events.append(_frame("artifact_error", job, request_id, {"message": job.error}))
        job.event_signal.set()
    finally:
        job.event_signal.set()


def _with_request_id(frame: dict[str, Any], request_id: str) -> dict[str, Any]:
    data = dict(frame.get("data") or {})
    data["request_id"] = request_id
    return {**frame, "data": data}


async def _generate_report_artifact_frames(
    *,
    job: ReportJob,
    request_id: str,
) -> AsyncIterator[dict[str, Any]]:
    if job.status == "ready":
        yield _frame("start", job, request_id, {"query": job.intent or job.title})
        yield _frame("step", job, request_id, {"turn": 1})
        yield _frame("artifact_start", job, request_id, {"type": "html", "title": job.title, "status": job.status})
        yield _frame("artifact_html_delta", job, request_id, {"delta": job.html})
        yield _frame("step_end", job, request_id, {"turn": 1, "has_tool_calls": False, "elapsed_ms": 0})
        yield _frame("artifact_ready", job, request_id, {"html_length": len(job.html)})
        yield _frame("artifact_export_ready", job, request_id, {"exports": job.exports or _export_urls(job.id)})
        return

    if job.status == "failed":
        yield _frame("start", job, request_id, {"query": job.intent or job.title})
        yield _frame("step", job, request_id, {"turn": 1})
        yield _frame("artifact_start", job, request_id, {"type": "html", "title": job.title, "status": job.status})
        yield _frame("artifact_error", job, request_id, {"message": job.error or "HTML report generation failed."})
        yield _frame("step_end", job, request_id, {"turn": 1, "has_tool_calls": False, "elapsed_ms": 0})
        return

    job.status = "running"
    job.error = ""
    job.html = ""
    job.exports = {}
    started_at = time.perf_counter()
    yield _frame("start", job, request_id, {"query": job.intent or job.title})
    yield _frame("step", job, request_id, {"turn": 1})
    yield _frame(
        "thinking",
        job,
        request_id,
        {"delta": "Preparing uploaded file context and starting streamed HTML generation."},
    )
    yield _frame("text", job, request_id, {"delta": "Starting streamed HTML report generation.\n\n"})

    file_context_call_id = f"call_{uuid.uuid4().hex[:8]}"
    yield _frame(
        "tool_call_start",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": file_context_call_id,
            "tool": "prepare_uploaded_file_context",
            "arguments": {
                "file_ids": job.file_ids,
                "file_count": len(job.file_briefs),
                "skill": job.skill,
            },
        },
    )
    yield _frame(
        "tool_result",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": file_context_call_id,
            "tool": "prepare_uploaded_file_context",
            "ok": True,
            "elapsed_ms": 0,
            "result": {
                "files": [
                    {
                        "filename": file.get("filename", ""),
                        "sheets": len(file.get("sheets", [])),
                        "has_text_preview": bool(str(file.get("text_preview", "")).strip()),
                    }
                    for file in job.file_briefs
                ],
            },
        },
    )
    yield _frame(
        "text",
        job,
        request_id,
        {"delta": f"Prepared {len(job.file_briefs)} uploaded or historical file(s). Streaming HTML now.\n\n"},
    )
    yield _frame("artifact_start", job, request_id, {"type": "html", "title": job.title, "status": job.status})

    llm_call_id = f"call_{uuid.uuid4().hex[:8]}"
    yield _frame(
        "tool_call_start",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": llm_call_id,
            "tool": "generate_html_report",
            "arguments": {
                "skill": job.skill,
                "intent": job.intent,
                "title": job.title,
                "file_count": len(job.file_briefs),
            },
        },
    )

    llm = create_llm_from_env(required=False)
    if llm is None:
        async for frame in _fail_report_generation(
            job=job,
            request_id=request_id,
            tool_call_id=llm_call_id,
            started_at=started_at,
            message="LLM is not configured. Configure LLM_API_KEY and LLM_MODEL before generating an HTML report.",
        ):
            yield frame
        return

    try:
        streamed_chunks = 0
        raw_parts: list[str] = []
        async for chunk in llm.chat_stream(
            _build_agent_messages(job),
            temperature=0.25,
            max_tokens=8192,
        ):
            if chunk.reasoning_content:
                yield _frame("thinking", job, request_id, {"delta": chunk.reasoning_content})
            if not chunk.content:
                continue
            raw_parts.append(chunk.content)
            job.html += chunk.content
            streamed_chunks += 1
            yield _frame("artifact_html_delta", job, request_id, {"delta": chunk.content})

        final_html = extract_model_html("".join(raw_parts), title=job.title)
        if final_html != job.html:
            job.html = final_html
            yield _frame("artifact_html_delta", job, request_id, {"delta": final_html, "replace": True})

        yield _frame(
            "tool_result",
            job,
            request_id,
            {
                "turn": 1,
                "tool_call_id": llm_call_id,
                "tool": "generate_html_report",
                "ok": True,
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                "result": {
                    "mode": "llm_stream",
                    "html_chars": len(job.html),
                    "chunks": streamed_chunks,
                },
            },
        )
        yield _frame("text", job, request_id, {"delta": "The model streamed HTML into the right-side artifact panel.\n\n"})
    except Exception as exc:
        logger.warning("report_html_generation_failed report_id=%s error=%s", job.id, exc)
        async for frame in _fail_report_generation(
            job=job,
            request_id=request_id,
            tool_call_id=llm_call_id,
            started_at=started_at,
            message=f"HTML report generation failed: {exc}",
        ):
            yield frame
        return
    finally:
        close = getattr(llm, "close", None)
        if close is not None:
            with suppress(Exception):
                await close()

    job.status = "ready"
    job.finished_at = _utc_iso()
    job.exports = _export_urls(job.id)
    yield _frame(
        "text",
        job,
        request_id,
        {"delta": "\nHTML report is complete. Export conversion runs only after choosing a download format."},
    )
    yield _frame(
        "step_end",
        job,
        request_id,
        {
            "turn": 1,
            "has_tool_calls": True,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        },
    )
    yield _frame("artifact_ready", job, request_id, {"html_length": len(job.html)})
    yield _frame("artifact_export_ready", job, request_id, {"exports": job.exports})


async def _fail_report_generation(
    *,
    job: ReportJob,
    request_id: str,
    tool_call_id: str,
    started_at: float,
    message: str,
) -> AsyncIterator[dict[str, Any]]:
    job.status = "failed"
    job.error = message
    job.finished_at = _utc_iso()
    yield _frame(
        "tool_result",
        job,
        request_id,
        {
            "turn": 1,
            "tool_call_id": tool_call_id,
            "tool": "generate_html_report",
            "ok": False,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "error_type": "html_generation_failed",
            "result": {"message": message},
        },
    )
    yield _frame("text", job, request_id, {"delta": f"{message}\n"})
    yield _frame("artifact_error", job, request_id, {"message": message})
    yield _frame(
        "step_end",
        job,
        request_id,
        {
            "turn": 1,
            "has_tool_calls": True,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        },
    )


def _build_agent_messages(job: ReportJob) -> list[Message]:
    system_prompt = _skill_prompt(job.skill)
    file_context = _format_file_context(job.file_briefs)
    user_prompt = (
        f"User request:\n{job.intent or job.title}\n\n"
        f"Report title:\n{job.title}\n\n"
        f"Uploaded file context:\n{file_context or 'No uploaded file context was provided.'}\n\n"
        "Return a complete, standalone HTML document only. Requirements:\n"
        "- Include <!doctype html>, <html>, <head>, inline <style>, and <body>.\n"
        "- Use professional financial-report layout: cover/header, KPI cards, tables, narrative sections, and charts when useful.\n"
        "- Use inline SVG or pure HTML/CSS charts; do not use scripts, external CSS, external fonts, or network assets.\n"
        "- Ground all numbers in the uploaded context. If data is missing, state that it is unavailable.\n"
        "- Do not wrap the answer in markdown fences and do not include explanatory text outside HTML."
    )
    return [Message.system(system_prompt), Message.user(user_prompt)]


def _skill_prompt(skill: str) -> str:
    if skill == "data_analysis":
        return (
            "You are an agentic financial/data analysis report designer. Generate the report as polished standalone "
            "HTML, driven by the user's prompt and the uploaded file context. Prefer concise Chinese business writing "
            "unless the user asks for another language. Do not invent numbers."
        )
    return (
        "You are an agentic HTML report designer. Follow the user's prompt closely, use uploaded file context when "
        "present, and output a polished standalone HTML artifact."
    )


def extract_model_html(content: str, *, title: str = "Report") -> str:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:html)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    doctype_idx = text.lower().find("<!doctype")
    html_idx = text.lower().find("<html")
    start_candidates = [idx for idx in (doctype_idx, html_idx) if idx >= 0]
    if start_candidates:
        start = min(start_candidates)
        end = text.lower().rfind("</html>")
        text = text[start : end + len("</html>")] if end >= start else text[start:]
    elif "<" in text and ">" in text:
        text = _html_document(title, text)
    else:
        raise ValueError("model response did not contain HTML")
    return _sanitize_html(text)


def _html_document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, "Microsoft YaHei", "PingFang SC", Arial, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #eef2f7; color: #172033; line-height: 1.65; }}
    .page {{ width: min(100%, 920px); margin: 0 auto; background: #fff; min-height: 100vh; padding: 48px 56px; }}
    @media print {{ body {{ background: #fff; }} .page {{ width: auto; padding: 0; }} }}
    @media (max-width: 760px) {{ .page {{ padding: 30px 22px; }} }}
  </style>
</head>
<body>
  <main class="page">
    {body}
  </main>
</body>
</html>"""


def _format_file_context(file_briefs: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for index, file in enumerate(file_briefs, start=1):
        lines = [
            f"File {index}: {file.get('filename', '')}",
            f"file_id={file.get('file_id', '')}",
            f"size_bytes={file.get('size_bytes', '')}",
        ]
        page_count = int(file.get("page_count") or 0)
        if page_count:
            lines.append(f"page_count={page_count}")
        text_preview = str(file.get("text_preview", "")).strip()
        if text_preview:
            lines.append("text_preview:")
            lines.append(text_preview[:5000])
        for sheet in file.get("sheets", [])[:5]:
            rows = sheet.get("rows", []) if isinstance(sheet, dict) else []
            headers = sheet.get("headers", []) if isinstance(sheet, dict) else []
            lines.append(
                "sheet="
                f"{sheet.get('name', '')}; rows={sheet.get('row_count', 0)}; "
                f"columns={sheet.get('column_count', 0)}; headers={headers}"
            )
            for row in rows[:12]:
                lines.append("row=" + json.dumps(row, ensure_ascii=False))
        warnings = file.get("warnings", [])
        if warnings:
            lines.append("warnings=" + json.dumps(warnings, ensure_ascii=False))
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def _sanitize_html(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"\son[a-z]+\s*=\s*(['\"]).*?\1", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r"\s(?:href|src)\s*=\s*(['\"])\s*javascript:.*?\1", "", value, flags=re.DOTALL | re.IGNORECASE)
    return value


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _frame(
    event: str,
    job: ReportJob,
    request_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "artifact_id": job.id,
        "report_id": job.id,
        "conversation_id": job.conversation_id,
        "request_id": request_id,
        **data,
    }
    return {"event": event, "data": payload}


def _export_urls(report_id: str) -> dict[str, str]:
    return {
        "html": f"/api/reports/{report_id}/exports/html",
        "pdf": f"/api/reports/{report_id}/exports/pdf",
        "word": f"/api/reports/{report_id}/exports/word",
        "md": f"/api/reports/{report_id}/exports/md",
    }


def _file_brief(record: ReportFileRecord) -> dict[str, Any]:
    parsed = record.parsed.model_dump(mode="json")
    return {
        "file_id": record.id,
        "filename": record.filename,
        "size_bytes": record.size_bytes,
        "sheets": parsed.get("sheets", []),
        "text_preview": parsed.get("text_preview", ""),
        "page_count": parsed.get("page_count", 0),
        "warnings": parsed.get("warnings", []),
    }
