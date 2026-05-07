from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent_core.errors import error_to_dict
from agent_core.tools.builtin import ReadFileTool
from services.agent_orchestration_service import AgentOrchestrationService


REPO_ROOT = Path(__file__).resolve().parents[2]


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

app = FastAPI(title="Agent Core Web API")
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


@app.get("/api/runs/stream")
async def run_stream(
    query: str = Query(..., min_length=1),
    agent_name: str = Query("deep_research", min_length=1),
    conversation_id: str = Query("web-conversation", min_length=1),
) -> StreamingResponse:
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query must not be empty")

    async def events() -> AsyncIterator[str]:
        async for payload in _run_agent_events(
            query=query,
            agent_name=agent_name,
            conversation_id=conversation_id,
        ):
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_agent_events(
    *,
    query: str,
    agent_name: str,
    conversation_id: str,
) -> AsyncIterator[dict[str, Any]]:
    service = AgentOrchestrationService(config_path=REPO_ROOT / "config" / "agents.yaml")
    request_id = f"web-{uuid.uuid4().hex[:12]}"
    context, event_stream = service.create_streaming_context(
        request_id=request_id,
        query=query,
        conversation_id=conversation_id,
    )

    if context.tool_collection.get("read_file") is None:
        context.tool_collection.add(ReadFileTool(workspace_root=REPO_ROOT))

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
