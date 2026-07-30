from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.base.context import AgentContext
from agentengine.llm.interfaces import LLMResponse
from mock_llm import MockLLMClient
from agentengine.stream.printer import Printer
from agentengine.stream.sse_queue import SseEventQueue
from agentengine.tools.base import Tool
from agentengine.tools.collection import ToolCollection


@pytest.fixture(autouse=True)
def _isolated_report_file_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own ReportFileStore + SQLite DB.

    The module-level ``REPORT_FILE_STORE`` in ``web_api`` would otherwise
    leak file_refs (and parsed blobs) between tests, breaking assertions
    that count uploads inside a single conversation.
    """
    try:
        from app.backend.services import web_api
        from app.backend.services.reporting.db import ReportMetadataDB
        from app.backend.services.reporting.file_store import ReportFileStore
        from app.backend.services.reporting.jobs import ReportJobStore
    except Exception:
        return
    upload_root = tmp_path / "report_uploads"
    metadata_db = ReportMetadataDB(upload_root / "index.db")
    monkeypatch.setattr(web_api, "REPORT_FILES_DB", metadata_db)
    monkeypatch.setattr(web_api, "REPORT_FILE_STORE", ReportFileStore(upload_root, db=metadata_db))
    monkeypatch.setattr(
        web_api,
        "REPORT_STORE",
        ReportJobStore(db=metadata_db, reports_dir=upload_root / "reports"),
    )


@pytest.fixture(autouse=True)
def _authenticated_web_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep legacy route tests focused on their endpoint contract.

    Production deliberately fails closed when OIDC is absent; authentication
    behavior itself is covered in ``test_web_auth.py`` with real JWTs.
    """
    try:
        from app.backend.services import web_api
        from app.backend.services.auth import Principal
    except Exception:
        return

    class _TestAuthenticator:
        async def authenticate(self, authorization: str | None) -> Principal:
            return Principal(
                subject="test-user",
                tenant_id="test-tenant",
                scopes=frozenset({
                    "agent:read", "agent:run", "reports:read", "reports:write",
                    "skills:read", "skills:manage", "connectors:manage", "approvals:decide",
                }),
            )

    monkeypatch.setattr(web_api, "AUTHENTICATOR", _TestAuthenticator())


class _EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, **kwargs):
        return kwargs.get("text", "echo")


@pytest.fixture
def event_stream() -> SseEventQueue:
    return SseEventQueue()


@pytest.fixture
def printer(event_stream: SseEventQueue) -> Printer:
    return Printer(request_id="test-001", event_stream=event_stream)


@pytest.fixture
def tool_collection() -> ToolCollection:
    coll = ToolCollection()
    coll.add(_EchoTool())
    return coll


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
def agent_context(printer: Printer, tool_collection: ToolCollection, mock_llm: MockLLMClient) -> AgentContext:
    return AgentContext(
        request_id="test-001",
        query="test query",
        llm=mock_llm,
        printer=printer,
        tool_collection=tool_collection,
    )


@pytest.fixture
def assistant_response() -> LLMResponse:
    return LLMResponse(content="I found the answer.", finish_reason="stop")


@pytest.fixture
def tool_call_response() -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            }
        ],
    )
