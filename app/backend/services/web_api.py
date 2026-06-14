from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse

from app.backend.agents import REGISTRY as AGENT_REGISTRY
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
from agentengine.skills.catalog import (
    LocalCatalogProvider,
    SkillCatalog,
    SkillCatalogError,
)
from agentengine.skills.registry import (
    SkillImportError,
    SkillRegistry,
)
from agentengine.sandbox import SandboxConfig, SandboxManager, SandboxedBashTool, SandboxedPythonTool
from agentengine.tools.builtin import (
    build_default_tools,
    ReadFileTool,
    ReadSkillResource,
    RunSkillScript,
    SkillTool,
)
from agentengine.tools.policy_presets import HOST_DEFAULT as DEFAULT_EXEC_POLICY
from app.backend.services.reporting.file_store import ReportFileStore
from app.backend.services.agent_orchestration_service import AgentOrchestrationService
from app.backend.services.expert_catalog import ExpertCatalog
from app.backend.services.mcp_connectors import McpConnector, McpConnectorStore
from app.backend.services.reporting.db import ReportMetadataDB
from app.backend.services.reporting.docx_renderer import render_docx
from app.backend.services.reporting.jobs import ReportJobStore, stream_report_artifact
from app.backend.services.reporting.pdf_renderer import PdfRendererUnavailable, render_pdf


REPO_ROOT = Path(__file__).resolve().parents[3]
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
DEFAULT_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
PERSISTENCE = SqlitePersistence(DEFAULT_DB_PATH)
LOCK_MANAGER = InMemoryConversationLockManager()
SANDBOX_MANAGER: SandboxManager | None = None
try:
    _sandbox_sessions = REPO_ROOT / "data" / "sandbox-sessions"
    SANDBOX_MANAGER = SandboxManager(sessions_root=_sandbox_sessions)
    import logging
    _logger = logging.getLogger(__name__)
    _logger.info("sandbox_manager_ready sessions_root=%s", _sandbox_sessions)
except Exception:
    import logging
    _logger = logging.getLogger(__name__)
    _logger.warning("sandbox_manager_unavailable — bash/python will run on host")
QUOTA_STORE = QuotaStore()
APPROVAL_GATE = ApprovalGate(timeout_seconds=float(os.getenv("APPROVAL_TIMEOUT_SECONDS", "300")))
REPORT_FILES_DB = ReportMetadataDB(DEFAULT_UPLOAD_ROOT / "index.db")
REPORT_STORE = ReportJobStore(
    db=REPORT_FILES_DB,
    reports_dir=DEFAULT_UPLOAD_ROOT / "reports",
)
REPORT_FILE_STORE = ReportFileStore(DEFAULT_UPLOAD_ROOT)
SKILL_REGISTRY = SkillRegistry(cwd=REPO_ROOT, db_path=DEFAULT_DB_PATH)
SKILL_CATALOG = SkillCatalog(provider=LocalCatalogProvider(), registry=SKILL_REGISTRY)
EXPERT_CATALOG = ExpertCatalog(
    markdown_dir=REPO_ROOT / "app" / "backend" / "agents" / "markdown",
    registry=AGENT_REGISTRY,
    teams_path=REPO_ROOT / "app" / "backend" / "services" / "experts_data" / "teams.json",
)
MCP_STORE = McpConnectorStore(REPO_ROOT / ".agent" / "mcp_connectors.json")

MAX_SKILL_ZIP_BYTES = 20 * 1024 * 1024  # 20MB ceiling for an uploaded skill pack.

app = FastAPI(title="AgentEngine Web API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def _shutdown_sandbox() -> None:
    if SANDBOX_MANAGER is not None:
        SANDBOX_MANAGER.shutdown()


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
    """Return the agents, tools, and *enabled* skills the web UI can surface.

    Disabled skills are deliberately omitted here — this endpoint feeds the
    "currently active capabilities" sidebar. The full list (enabled +
    disabled) is served by ``GET /api/skills`` for the management UI.
    """
    skill_loader = SkillLoader(cwd=REPO_ROOT)
    tools = build_default_tools(workspace_root=REPO_ROOT)
    tools.append(SkillTool(skill_loader))
    tools.append(ReadSkillResource(skill_loader))
    if SANDBOX_MANAGER is not None:
        tools.append(RunSkillScript(
            loader=skill_loader,
            sandbox_manager=SANDBOX_MANAGER,
            conversation_id="capabilities",
            workspace_root=SANDBOX_MANAGER.sessions_root,
        ))
    skills = [view for view in await SKILL_REGISTRY.list() if view.enabled]
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
            {"name": view.name, "description": view.description}
            for view in skills
        ],
    }


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    """List every installed skill with its enable state and provenance."""
    views = await SKILL_REGISTRY.list()
    return {"skills": [view.snapshot() for view in views]}


@app.get("/api/skills/{name}")
async def skill_detail(name: str) -> dict[str, Any]:
    """Return a single skill including its SKILL.md body for preview."""
    detail = await SKILL_REGISTRY.get(name)
    if detail is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return detail.snapshot()


@app.post("/api/skills/import")
async def import_skill(
    request: Request,
    filename: str = Query("", min_length=0),
) -> dict[str, Any]:
    """Install a skill from an uploaded ``.zip`` (raw bytes in the body)."""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="请求体为空，请上传 .zip 文件")
    if len(data) > MAX_SKILL_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="skill 包超过 20MB 上限")
    try:
        view = await SKILL_REGISTRY.import_zip(data, original_filename=filename)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"技能「{exc.args[0] if exc.args else filename}」已存在，请先删除再导入",
        ) from exc
    except SkillImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return view.snapshot()


@app.patch("/api/skills/{name}")
async def update_skill(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Enable or disable an installed skill."""
    if "enabled" not in body:
        raise HTTPException(status_code=400, detail="缺少 enabled 字段")
    enabled = bool(body["enabled"])
    try:
        view = await SKILL_REGISTRY.set_enabled(name, enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    return view.snapshot()


@app.delete("/api/skills/{name}")
async def delete_skill(name: str) -> dict[str, Any]:
    """Delete an imported skill (builtin skills are protected)."""
    try:
        await SKILL_REGISTRY.delete(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail="内建技能不可删除，只能禁用",
        ) from exc
    return {"ok": True, "name": name}


@app.get("/api/skills/{name}/export")
async def export_skill(name: str) -> Response:
    """Download an installed skill as a ``.zip`` archive."""
    try:
        data = await SKILL_REGISTRY.export_zip(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


# -- Skill marketplace (browse + install) ------------------------------------


@app.get("/api/skill-market")
async def list_skill_market(
    category: str = Query("", min_length=0),
    q: str = Query("", min_length=0),
) -> dict[str, Any]:
    """List installable marketplace skills, flagged with their install state."""
    views = await SKILL_CATALOG.list(category=category or None, query=q or None)
    return {"skills": [view.snapshot() for view in views]}


@app.get("/api/skill-market/categories")
async def list_skill_market_categories() -> dict[str, Any]:
    """Return the marketplace category filters (with an "all" entry first)."""
    return {"categories": await SKILL_CATALOG.categories()}


@app.post("/api/skill-market/{entry_id}/install")
async def install_skill_from_market(entry_id: str) -> dict[str, Any]:
    """Install a marketplace skill into ``.agent/skills``."""
    try:
        view = await SKILL_CATALOG.install(entry_id)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"技能「{exc.args[0] if exc.args else entry_id}」已安装",
        ) from exc
    except (SkillCatalogError, SkillImportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return view.snapshot()


# -- Experts (runnable agent presets surfaced as a directory) ----------------


@app.get("/api/experts")
async def list_experts(
    category: str = Query("", min_length=0),
    scenario: str = Query("", min_length=0),
    q: str = Query("", min_length=0),
) -> dict[str, Any]:
    """List runnable experts (agent presets with display metadata)."""
    experts = EXPERT_CATALOG.list(
        category=category or None,
        scenario=scenario or None,
        query=q or None,
    )
    return {"experts": [expert.snapshot() for expert in experts]}


@app.get("/api/experts/categories")
async def list_expert_categories() -> dict[str, Any]:
    """Return the expert category filters (with an "all" entry first)."""
    return {"categories": EXPERT_CATALOG.categories()}


@app.get("/api/experts/scenarios")
async def list_expert_scenarios() -> dict[str, Any]:
    """Return featured scenario groups for the experts page."""
    return {"scenarios": [group.snapshot() for group in EXPERT_CATALOG.scenarios()]}


# -- Expert teams (sequential relay pipelines) -------------------------------


@app.get("/api/expert-teams")
async def list_expert_teams(
    category: str = Query("", min_length=0),
    q: str = Query("", min_length=0),
) -> dict[str, Any]:
    """List expert teams (ordered groups run as a relay pipeline)."""
    teams = EXPERT_CATALOG.list_teams(category=category or None, query=q or None)
    return {"teams": [team.snapshot() for team in teams]}


@app.get("/api/expert-teams/categories")
async def list_expert_team_categories() -> dict[str, Any]:
    """Return the expert-team category filters (with an "all" entry first)."""
    return {"categories": EXPERT_CATALOG.team_categories()}


@app.get("/api/expert-teams/{team_id}")
async def expert_team_detail(team_id: str) -> dict[str, Any]:
    """Return one expert team (ordered members) for the start-team confirm step."""
    team = EXPERT_CATALOG.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="expert team not found")
    return team.snapshot()


# -- MCP connectors (CRUD) ------------------------------------------------


@app.get("/api/connectors")
async def list_connectors() -> dict[str, Any]:
    """List all MCP connector configurations."""
    connectors = MCP_STORE.list_all()
    return {"connectors": [c.snapshot() for c in connectors]}


@app.post("/api/connectors")
async def add_connector(body: dict[str, Any]) -> dict[str, Any]:
    """Add a new MCP connector configuration."""
    name = str(body.get("name", "")).strip()
    transport = str(body.get("transport", "stdio")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="connector name is required")
    if transport not in ("stdio", "sse"):
        raise HTTPException(status_code=400, detail="transport must be 'stdio' or 'sse'")
    connector = McpConnector(
        id="",
        name=name,
        transport=transport,
        command=str(body.get("command", "")).strip(),
        args=[str(a) for a in body.get("args", []) or []],
        env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
        url=str(body.get("url", "")).strip(),
    )
    added = MCP_STORE.add(connector)
    return added.snapshot()


@app.delete("/api/connectors/{connector_id}")
async def delete_connector(connector_id: str) -> dict[str, Any]:
    """Remove an MCP connector configuration."""
    if not MCP_STORE.delete(connector_id):
        raise HTTPException(status_code=404, detail="connector not found")
    return {"deleted": True}


@app.patch("/api/connectors/{connector_id}/toggle")
async def toggle_connector(connector_id: str) -> dict[str, Any]:
    """Enable or disable an MCP connector."""
    updated = MCP_STORE.toggle(connector_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="connector not found")
    return updated.snapshot()


def _public_message_dto(message: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields safe to expose to the web UI.

    Internal ``metadata`` and large ``base64_image`` payloads are deliberately
    stripped.
    """
    dto: dict[str, Any] = {
        "role": message.get("role"),
        "content": message.get("content"),
    }
    for key in ("reasoning_content", "name", "tool_call_id", "tool_calls"):
        value = message.get(key)
        if value:
            dto[key] = value
    return dto


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
    visible = [_public_message_dto(m) for m in messages if m.get("role") != "system"]
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
    record = await REPORT_FILE_STORE.aget(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="report file not found")
    return record.snapshot()


@app.post("/api/reports")
async def create_report(body: dict[str, Any]) -> dict[str, Any]:
    """Create an HTML report artifact job.

    The artifact stream requires a configured LLM. The selected skill prompt,
    user intent, and uploaded file context produce a standalone HTML artifact
    through the model streaming API.
    """
    conversation_id = str(body.get("conversation_id") or "web-conversation").strip()
    title = str(body.get("title") or "数据分析").strip()
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
        await REPORT_FILE_STORE.aget_many(file_ids)
        if file_ids
        else await REPORT_FILE_STORE.alist_by_conversation(scoped_conversation_id)
    )
    missing = sorted(set(file_ids) - {record.id for record in files})
    if missing:
        raise HTTPException(status_code=404, detail={"missing_file_ids": missing})

    resume_from_html = ""
    resume_from_id = str(body.get("resume_from_report_id") or "").strip()
    if resume_from_id:
        prior_job = await REPORT_STORE.aload(resume_from_id)
        if prior_job is not None and prior_job.html.strip():
            resume_from_html = prior_job.html

    job = await REPORT_STORE.acreate(
        conversation_id=conversation_id,
        title=title,
        intent=intent,
        skill=skill,
        files=files,
        resume_from_html=resume_from_html,
    )
    return job.snapshot()


@app.get("/api/reports")
async def list_reports(
    conversation_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List report metadata for a conversation, newest first."""
    rows = await REPORT_STORE.alist_by_conversation(
        conversation_id,
        limit=limit,
    )
    return {"reports": rows}


@app.get("/api/reports/{report_id}")
async def report_snapshot(report_id: str) -> dict[str, Any]:
    job = await REPORT_STORE.aload(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    return job.snapshot()


@app.get("/api/reports/{report_id}/stream")
async def report_stream(report_id: str) -> StreamingResponse:
    job = await REPORT_STORE.aload(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    request_id = f"report-{uuid.uuid4().hex[:12]}"

    async def events() -> AsyncIterator[str]:
        async for frame in stream_report_artifact(job=job, request_id=request_id, store=REPORT_STORE):
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
    job = await REPORT_STORE.aload(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    svg = job.chart_assets.get(chart_id)
    if svg is None:
        raise HTTPException(status_code=404, detail="chart not found")
    return Response(svg, media_type="image/svg+xml; charset=utf-8")


@app.get("/api/reports/{report_id}/exports/{export_format}")
async def report_export(report_id: str, export_format: str) -> Response:
    job = await REPORT_STORE.aload(report_id)
    if job is None:
        raise HTTPException(status_code=404, detail="report not found")
    if export_format not in {"md", "html", "word", "doc", "docx", "pdf"}:
        raise HTTPException(status_code=501, detail=f"{export_format} export is not implemented yet")
    if not job.html:
        raise HTTPException(status_code=409, detail=job.error or "report HTML is not ready")
    report_html = job.html
    if export_format == "md":
        return PlainTextResponse(
            _html_to_text(report_html),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
        )
    if export_format == "html":
        return HTMLResponse(
            report_html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.html"'},
        )
    if export_format in {"word", "doc", "docx"}:
        docx = render_docx(report_html)
        return Response(
            docx,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.docx"'},
        )
    if export_format == "pdf":
        try:
            pdf = render_pdf(report_html)
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
    skill: str = Query("", min_length=0),
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
            skill=skill.strip(),
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
    skill: str = "",
) -> AsyncIterator[dict[str, Any]]:
    service = AgentOrchestrationService(
        persistence=PERSISTENCE,
        lock_manager=LOCK_MANAGER,
        middleware=_build_enterprise_middleware(),
    )
    request_id = request_id or f"web-{uuid.uuid4().hex[:12]}"
    scoped_conversation_id = _scoped_conversation_id(conversation_id, tenant_id)

    # Resolve the enable-list once so disabled skills can't be invoked, and so a
    # skill the user explicitly selected but later disabled is ignored rather
    # than silently used.
    enabled_skills = await SKILL_REGISTRY.enabled_names()
    selected_skill = skill if skill and skill in enabled_skills else ""
    effective_query = _apply_skill_directive(query, selected_skill)

    context, event_stream = service.create_streaming_context(
        request_id=request_id,
        query=effective_query,
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

    # -- sandbox wiring ---------------------------------------------------
    sandbox = SANDBOX_MANAGER
    sandbox_ws: str | None = None
    if sandbox is not None:
        try:
            sandbox_ws = str(sandbox.host_workspace_for(scoped_conversation_id))
            context.extras["sandbox_manager"] = sandbox
            context.extras["workspace_root"] = sandbox_ws
        except Exception:
            sandbox = None

    # File tools: use sandbox workspace as extra root so /workspace paths resolve.
    if context.tool_collection.get("read_file") is None:
        extra_roots = [sandbox_ws] if sandbox_ws else []
        context.tool_collection.add(ReadFileTool(
            workspace_root=REPO_ROOT,
            extra_roots=extra_roots,
        ))
    skill_loader = SkillLoader(cwd=REPO_ROOT)
    if context.tool_collection.get("Skill") is None:
        context.tool_collection.add(
            SkillTool(skill_loader, enabled_names=enabled_skills)
        )
    if context.tool_collection.get("ReadSkillResource") is None:
        context.tool_collection.add(
            ReadSkillResource(skill_loader, enabled_names=enabled_skills)
        )

    # Shell tools: sandboxed when available, fallback to host (Phase 3).
    if sandbox is not None and sandbox_ws is not None:
        if context.tool_collection.get("RunSkillScript") is None:
            context.tool_collection.add(
                RunSkillScript(
                    loader=skill_loader,
                    sandbox_manager=sandbox,
                    conversation_id=scoped_conversation_id,
                    enabled_names=enabled_skills,
                    workspace_root=sandbox_ws,
                )
            )
        if context.tool_collection.get("bash") is None:
            context.tool_collection.add(
                SandboxedBashTool(manager=sandbox, conversation_id=scoped_conversation_id)
            )
        if context.tool_collection.get("python") is None:
            context.tool_collection.add(
                SandboxedPythonTool(manager=sandbox, conversation_id=scoped_conversation_id)
            )
    # When sandbox is unavailable, the agent preset's setup hook may still add
    # host-level BashTool — that's the graceful degradation path.

    # ExecPolicy: soft safety net. Sandbox is the real boundary; this catches
    # obviously malicious patterns even inside the container.
    if "exec_policy" not in context.extras:
        context.extras["exec_policy"] = DEFAULT_EXEC_POLICY

    async def run_and_close() -> None:
        try:
            await service.run(agent_name=agent_name, query=effective_query, context=context)
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
        # Note: sandbox is NOT released here — containers persist across runs
        # within a conversation (acquired on first use, reused thereafter).
        # Idle containers are cleaned up by SandboxManager.reap_idle().
        await service.close()


def _format_sse_frame(frame: dict[str, Any]) -> str:
    comment = frame.get("comment")
    if comment is not None:
        return "\n".join(f": {line}" for line in str(comment).splitlines()) + "\n\n"

    event = str(frame.get("event", "message"))
    data = json.dumps(frame.get("data", {}), ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _html_to_text(value: str) -> str:
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", value, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


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


def _apply_skill_directive(query: str, skill: str) -> str:
    """Prefix the query so the model invokes the selected skill via the Skill tool.

    ``chat`` (or empty) means "no specific skill" — the query is untouched.
    Otherwise we nudge the agent to call ``Skill(skill=<name>)`` first. The
    SkillTool itself enforces enable state, so a stale name degrades gracefully.
    """
    if not skill or skill == "chat":
        return query
    return (
        f"请先调用 Skill 工具（skill=\"{skill}\"）加载该技能，并严格按其说明处理以下请求：\n\n"
        f"{query}"
    )


def _parse_scopes(raw: str) -> list[str]:
    return [scope.strip() for scope in raw.split(",") if scope.strip()]


def _scoped_conversation_id(conversation_id: str, tenant_id: str) -> str:
    tenant = tenant_id.strip() or "default"
    if tenant == "default":
        return conversation_id
    return f"{tenant}:{conversation_id}"
