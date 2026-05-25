from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse

from examples.agents import REGISTRY as AGENT_REGISTRY
from agentengine.concurrency import InMemoryConversationLockManager
from agentengine.enterprise import (
    ApprovalGate,
    ApprovalResult,
    EnvSecrets,
    MiddlewareChain,
    QuotaLimits,
    QuotaStore,
    TenantContext,
    approval_middleware,
    otel_tracing_middleware,
    quota_middleware,
    retry_middleware,
)
from agentengine.errors import error_to_dict
from agentengine.persistence import SqlitePersistence
from agentengine.skills.loader import SkillLoader
from agentengine.tools.builtin import build_default_tools, ReadFileTool, SkillTool
from examples.services.reporting.file_store import ReportFileStore
from examples.services.agent_orchestration_service import AgentOrchestrationService
from examples.services.reporting.docx_renderer import DOCX_MEDIA_TYPE, render_docx
from examples.services.reporting.html_renderer import render_html
from examples.services.reporting.jobs import ReportJobStore, stream_report_artifact
from examples.services.reporting.markdown_renderer import render_markdown
from examples.services.reporting.pdf_renderer import PdfRendererUnavailable, render_pdf


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "chatbot.db"
DEFAULT_UPLOAD_ROOT = REPO_ROOT / "data" / "uploads"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")

# Process-wide singletons: persistence + lock manager. The Service constructed
# per request must share the same instances so all conversations land in one
# database and concurrent runs hit the same locks.
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
PERSISTENCE = SqlitePersistence(DEFAULT_DB_PATH)
LOCK_MANAGER = InMemoryConversationLockManager()
QUOTA_STORE = QuotaStore()
APPROVAL_GATE = ApprovalGate(timeout_seconds=float(os.getenv("APPROVAL_TIMEOUT_SECONDS", "300")))
REPORT_STORE = ReportJobStore()
REPORT_FILE_STORE = ReportFileStore(DEFAULT_UPLOAD_ROOT)

app = FastAPI(title="AgentEngine Web API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "llm_configured": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
        "model": os.getenv("LLM_MODEL", ""),
    }


@app.post("/api/approvals/{approval_id}")
async def approval_decision(
    approval_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a pending destructive-tool approval request."""
    approved = bool(body.get("approved"))
    reason = str(body.get("reason", ""))
    resolved = APPROVAL_GATE.resolve(
        approval_id,
        ApprovalResult(approved=approved, reason=reason),
    )
    if not resolved:
        raise HTTPException(status_code=404, detail="approval request not found")
    return {"ok": True, "approval_id": approval_id, "approved": approved}


@app.get("/api/capabilities")
async def capabilities() -> dict[str, Any]:
    """Return the agents, tools, and skills that the web UI can surface."""
    skill_loader = SkillLoader(cwd=REPO_ROOT)
    skills = skill_loader.discover()
    tools = build_default_tools(workspace_root=REPO_ROOT)
    tools.append(SkillTool(skill_loader))
    return {
        "agents": [
            {"name": name, "description": spec.description}
            for name, spec in sorted(AGENT_REGISTRY.items())
        ],
        "tools": [
            {"name": tool.name, "description": tool.description}
            for tool in tools
        ],
        "skills": [
            {"name": skill.name, "description": skill.description}
            for skill in sorted(skills.values(), key=lambda item: item.name)
        ],
    }


@app.get("/api/conversations/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: str,
    tenant_id: str = Query("default", min_length=1),
) -> dict[str, Any]:
    """Return persisted messages for a conversation so the UI can rehydrate.

    Filters out system messages (prompts injected by the agent itself) and
    only returns user/assistant/tool turns the front end actually displays.
    """
    if not conversation_id.strip():
        raise HTTPException(status_code=400, detail="conversation_id is required")
    scoped_conversation_id = _scoped_conversation_id(conversation_id, tenant_id)
    messages = await PERSISTENCE.load_messages(scoped_conversation_id)
    visible = [m for m in messages if m.get("role") != "system"]
    return {"conversation_id": conversation_id, "messages": visible}


@app.post("/api/report-files")
async def upload_report_file(
    request: Request,
    filename: str = Query(..., min_length=1),
    conversation_id: str = Query("web-conversation", min_length=1),
    tenant_id: str = Query("default", min_length=1),
) -> dict[str, Any]:
    data = await request.body()
    try:
        record = await REPORT_FILE_STORE.save_bytes(
            data=data,
            filename=filename,
            tenant_id=tenant_id,
            conversation_id=_scoped_conversation_id(conversation_id, tenant_id),
        )
    except ValueError as exc:
        message = str(exc)
        if "unsupported file extension" in message:
            raise HTTPException(status_code=415, detail=message) from exc
        if "50MB" in message:
            raise HTTPException(status_code=413, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    return record.snapshot()


@app.get("/api/report-files/{file_id}")
async def report_file_snapshot(file_id: str) -> dict[str, Any]:
    record = REPORT_FILE_STORE.get(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="report file not found")
    return record.snapshot()


@app.post("/api/reports")
async def create_report(body: dict[str, Any]) -> dict[str, Any]:
    """Create a financial-report artifact job.

    The artifact stream is model-first when LLM_* is configured: selected skill
    prompt + user intent + uploaded file context produce report blocks. The
    deterministic report remains the offline fallback for local demos/tests.
    """
    conversation_id = str(body.get("conversation_id") or "web-conversation").strip()
    title = str(body.get("title") or "财务分析报告").strip()
    intent = str(body.get("intent") or body.get("query") or "").strip()
    skill = str(body.get("skill") or "data_analysis").strip() or "data_analysis"
    file_ids = [str(item) for item in body.get("file_ids") or [] if str(item).strip()]
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    scoped_conversation_id = _scoped_conversation_id(
        conversation_id,
        str(body.get("tenant_id") or "default"),
    )
    files = (
        REPORT_FILE_STORE.get_many(file_ids)
        if file_ids
        else REPORT_FILE_STORE.list_by_conversation(scoped_conversation_id)
    )
    missing = sorted(set(file_ids) - {record.id for record in files})
    if missing:
        raise HTTPException(status_code=404, detail={"missing_file_ids": missing})
    job = REPORT_STORE.create(
        conversation_id=conversation_id,
        title=title,
        intent=intent,
        skill=skill,
        files=files,
    )
    return job.snapshot()


@app.get("/api/reports/{report_id}")
async def report_snapshot(report_id: str) -> dict[str, Any]:
    job = REPORT_STORE.get(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    return job.snapshot()


@app.get("/api/reports/{report_id}/stream")
async def report_stream(report_id: str) -> StreamingResponse:
    job = REPORT_STORE.get(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    request_id = f"report-{uuid.uuid4().hex[:12]}"

    async def events() -> AsyncIterator[str]:
        async for frame in stream_report_artifact(job=job, request_id=request_id):
            yield _format_sse_frame(frame)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Streaming-Protocol": "agent-core.sse.v2",
            "X-Request-ID": request_id,
            "X-Conversation-ID": job.conversation_id,
        },
    )


@app.get("/api/reports/{report_id}/charts/{chart_id}.svg")
async def report_chart_svg(report_id: str, chart_id: str) -> Response:
    job = REPORT_STORE.get(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    svg = job.chart_assets.get(chart_id)
    if svg is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return Response(svg, media_type="image/svg+xml; charset=utf-8")


@app.get("/api/reports/{report_id}/exports/{export_format}")
async def report_export(report_id: str, export_format: str) -> Response:
    job = REPORT_STORE.get(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    if export_format == "md":
        return PlainTextResponse(
            render_markdown(job.ir),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
        )
    if export_format == "html":
        return HTMLResponse(
            render_html(job.ir, chart_assets=job.chart_assets),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.html"'},
        )
    if export_format == "docx":
        return Response(
            render_docx(job.ir, chart_assets=job.chart_assets),
            media_type=DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{report_id}.docx"'},
        )
    if export_format == "pdf":
        html = render_html(job.ir, chart_assets=job.chart_assets)
        try:
            pdf = render_pdf(html)
        except PdfRendererUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'},
        )
    raise HTTPException(status_code=501, detail=f"{export_format} export is not implemented yet")


@app.get("/api/runs/stream")
async def run_stream(
    query: str = Query(..., min_length=1),
    agent_name: str = Query("deep_research", min_length=1),
    conversation_id: str = Query("web-conversation", min_length=1),
    tenant_id: str = Query("default", min_length=1),
    user_id: str = Query("", min_length=0),
    scopes: str = Query("", min_length=0),
) -> StreamingResponse:
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")
    request_id = f"web-{uuid.uuid4().hex[:12]}"

    async def events() -> AsyncIterator[str]:
        async for frame in _run_agent_events(
            query=query,
            agent_name=agent_name,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            user_id=user_id,
            scopes=_parse_scopes(scopes),
            request_id=request_id,
        ):
            yield _format_sse_frame(frame)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Streaming-Protocol": "agent-core.sse.v2",
            "X-Request-ID": request_id,
            "X-Conversation-ID": conversation_id,
            "X-Tenant-ID": tenant_id,
        },
    )


async def _run_agent_events(
    *,
    query: str,
    agent_name: str,
    conversation_id: str,
    tenant_id: str = "default",
    user_id: str = "",
    scopes: list[str] | None = None,
    request_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    service = AgentOrchestrationService(
        persistence=PERSISTENCE,
        lock_manager=LOCK_MANAGER,
        middleware=_build_enterprise_middleware(),
    )
    request_id = request_id or f"web-{uuid.uuid4().hex[:12]}"
    scoped_conversation_id = _scoped_conversation_id(conversation_id, tenant_id)
    context, event_stream = service.create_streaming_context(
        request_id=request_id,
        query=query,
        conversation_id=scoped_conversation_id,
    )
    if context.printer is not None:
        context.printer.conversation_id = conversation_id
    context.extras["tenant"] = TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=scoped_conversation_id,
        scopes=scopes or [],
    )
    context.extras["secrets"] = EnvSecrets()
    context.extras["public_conversation_id"] = conversation_id

    if context.tool_collection.get("read_file") is None:
        context.tool_collection.add(ReadFileTool(workspace_root=REPO_ROOT))
    if context.tool_collection.get("Skill") is None:
        context.tool_collection.add(SkillTool(SkillLoader(cwd=REPO_ROOT)))

    async def run_and_close() -> None:
        try:
            await service.run(agent_name=agent_name, query=query, context=context)
        except Exception as exc:
            if context.printer and "agent" not in context.extras:
                await context.printer.error(error_to_dict(exc))
        finally:
            await event_stream.close()

    task = asyncio.create_task(run_and_close())
    try:
        async for event in event_stream:
            yield event
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            await task
        await service.close()


def _format_sse_frame(frame: dict[str, Any]) -> str:
    comment = frame.get("comment")
    if comment is not None:
        return "\n".join(f": {line}" for line in str(comment).splitlines()) + "\n\n"

    event = str(frame.get("event", "message"))
    data = json.dumps(frame.get("data", {}), ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _build_enterprise_middleware() -> MiddlewareChain:
    _configure_quota_from_env(QUOTA_STORE)
    return MiddlewareChain([
        otel_tracing_middleware(),
        quota_middleware(QUOTA_STORE),
        retry_middleware(),
        approval_middleware(APPROVAL_GATE),
    ])


def _configure_quota_from_env(store: QuotaStore) -> None:
    default_limits = QuotaLimits(
        max_runs=_env_int("QUOTA_MAX_RUNS"),
        max_tool_calls=_env_int("QUOTA_MAX_TOOL_CALLS"),
        max_tokens_in=_env_int("QUOTA_MAX_TOKENS_IN"),
        max_tokens_out=_env_int("QUOTA_MAX_TOKENS_OUT"),
        window_seconds=float(os.getenv("QUOTA_WINDOW_SECONDS", "60")),
    )
    if any([
        default_limits.max_runs,
        default_limits.max_tool_calls,
        default_limits.max_tokens_in,
        default_limits.max_tokens_out,
    ]):
        store.set_limits("default", default_limits)


def _env_int(name: str) -> int:
    raw = os.getenv(name, "0").strip()
    return int(raw) if raw.isdigit() else 0


def _parse_scopes(raw: str) -> list[str]:
    return [scope.strip() for scope in raw.split(",") if scope.strip()]


def _scoped_conversation_id(conversation_id: str, tenant_id: str) -> str:
    tenant = tenant_id.strip() or "default"
    if tenant == "default":
        return conversation_id
    return f"{tenant}:{conversation_id}"
