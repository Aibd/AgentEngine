"""Integration tests for the FastAPI SSE endpoint.

The web frontend depends on these wire shapes — keep them stable.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from agentengine.llm.interfaces import LLMResponse
from agentengine.enterprise import ApprovalGate, QuotaLimits, QuotaStore
from mock_llm import MockLLMClient
from app.backend.services import web_api


pytestmark = pytest.mark.asyncio


async def _consume_sse(response: httpx.Response) -> AsyncIterator[dict]:
    """Yield v2 SSE frames parsed from a streaming response."""
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
            payload = "\n".join(data_lines)
            yield {"event": event_name, "data": json.loads(payload)}


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    """Force the orchestration service to use a mock LLM regardless of env."""

    def _factory_returns_mock():
        return MockLLMClient([
            LLMResponse(
                content="response from mock",
                finish_reason="stop",
                usage={"prompt_tokens": 7, "completion_tokens": 3},
            ),
        ])

    monkeypatch.setattr(web_api, "_default_llm_factory_for_tests", _factory_returns_mock, raising=False)
    # The web_api module reuses AgentOrchestrationService's default factory;
    # patch that directly so context.llm gets injected at request time.
    from app.backend.services import agent_orchestration_service as orch_module
    monkeypatch.setattr(orch_module, "_default_llm_factory", _factory_returns_mock)
    monkeypatch.setattr(web_api, "QUOTA_STORE", QuotaStore())
    monkeypatch.setattr(web_api, "APPROVAL_GATE", ApprovalGate(timeout_seconds=0.1))


async def test_health_endpoint() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "model" in body


async def test_capabilities_endpoint_lists_web_agent_tools_and_skills() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert {agent["name"] for agent in body["agents"]} >= {"general_chat", "deep_research"}
    assert {tool["name"] for tool in body["tools"]} >= {"read_file", "Skill"}
    assert any(skill["name"] == "codebase-research" for skill in body["skills"])


async def test_run_stream_emits_full_event_sequence() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        async with client.stream(
            "GET",
            "/api/runs/stream",
            params={
                "query": "hello",
                "agent_name": "general_chat",
                "conversation_id": "test-conv",
            },
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-streaming-protocol"] == "agent-core.sse.v2"
            assert response.headers["x-conversation-id"] == "test-conv"
            events: list[dict] = []
            async for evt in _consume_sse(response):
                events.append(evt)

    types = [e["event"] for e in events]
    assert types[0] == "start"
    assert "step" in types
    assert "step_end" in types
    assert "usage" in types
    assert types[-1] == "done"

    # Schema invariants the frontend relies on
    for evt in events:
        assert "event" in evt
        assert "data" in evt
        assert evt["data"]["request_id"].startswith("web-")
        assert evt["data"]["conversation_id"] == "test-conv"
        assert "responseType" not in evt["data"]


async def test_run_stream_rejects_empty_query() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/runs/stream",
            params={"query": "   ", "agent_name": "general_chat"},
        )
    # FastAPI's Query(min_length=1) returns 422 for whitespace-stripped empty.
    # Our explicit handler returns 400 if it slips through. Either is fine —
    # the frontend just needs a non-200.
    assert response.status_code in (400, 422)


async def test_run_stream_scopes_persistence_by_tenant() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        async with client.stream(
            "GET",
            "/api/runs/stream",
            params={
                "query": "tenant hello",
                "agent_name": "general_chat",
                "conversation_id": "tenant-test-conv",
                "tenant_id": "acme",
                "user_id": "u1",
            },
        ) as response:
            assert response.status_code == 200
            events = [evt async for evt in _consume_sse(response)]

        default_messages = await client.get("/api/conversations/tenant-test-conv/messages")
        tenant_messages = await client.get(
            "/api/conversations/tenant-test-conv/messages",
            params={"tenant_id": "acme"},
        )

    assert events[-1]["event"] == "done"
    assert default_messages.json()["messages"] == []
    assert any(
        msg["role"] == "user" and msg["content"] == "tenant hello"
        for msg in tenant_messages.json()["messages"]
    )


async def test_run_stream_emits_quota_error_for_limited_tenant() -> None:
    web_api.QUOTA_STORE.set_limits("limited", QuotaLimits(max_runs=1))
    await web_api.QUOTA_STORE.check_and_acquire_run("limited")

    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        async with client.stream(
            "GET",
            "/api/runs/stream",
            params={
                "query": "hello",
                "agent_name": "general_chat",
                "conversation_id": "quota-test-conv",
                "tenant_id": "limited",
            },
        ) as response:
            assert response.status_code == 200
            events = [evt async for evt in _consume_sse(response)]

    error = next(evt for evt in events if evt["event"] == "error")
    assert error["data"]["code"] == "quota_exceeded"
    assert error["data"]["details"]["tenant_id"] == "limited"


async def test_approval_decision_rejects_unknown_id() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/approvals/apr_missing",
            json={"approved": True},
        )

    assert response.status_code == 404


# -- Skills management API ---------------------------------------------------


import io
import zipfile
from pathlib import Path

from agentengine.skills.registry import SkillRegistry


def _skill_zip(name: str, description: str = "demo") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: {description}\n---\nBody for {name}",
        )
    return buffer.getvalue()


@pytest.fixture
def isolated_skill_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``web_api.SKILL_REGISTRY`` at a temp skills dir + DB.

    Seeds one builtin-style skill so list/capabilities have something to return
    without touching the repository's real ``.agent/skills``.
    """
    skills_dir = tmp_path / ".agent" / "skills"
    builtin = skills_dir / "seeded"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: seeded\ndescription: Seeded builtin\n---\nBody",
        encoding="utf-8",
    )
    registry = SkillRegistry(cwd=tmp_path, db_path=tmp_path / "skills_meta.db")
    monkeypatch.setattr(web_api, "SKILL_REGISTRY", registry)
    return skills_dir


async def test_list_skills_returns_seeded_skill(isolated_skill_registry: Path) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/skills")

    assert response.status_code == 200
    skills = response.json()["skills"]
    seeded = next(s for s in skills if s["name"] == "seeded")
    assert seeded["enabled"] is True
    assert seeded["source"] == "builtin"


async def test_import_then_export_skill(isolated_skill_registry: Path) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        imported = await client.post(
            "/api/skills/import?filename=translator.zip",
            content=_skill_zip("translator", "Translate text"),
            headers={"Content-Type": "application/zip"},
        )
        assert imported.status_code == 200
        body = imported.json()
        assert body["name"] == "translator"
        assert body["source"] == "imported"

        listed = await client.get("/api/skills")
        assert "translator" in {s["name"] for s in listed.json()["skills"]}

        export = await client.get("/api/skills/translator/export")
        assert export.status_code == 200
        assert export.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            assert "translator/SKILL.md" in archive.namelist()


async def test_import_duplicate_returns_409(isolated_skill_registry: Path) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post(
            "/api/skills/import?filename=dup.zip",
            content=_skill_zip("dup"),
            headers={"Content-Type": "application/zip"},
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/skills/import?filename=dup.zip",
            content=_skill_zip("dup"),
            headers={"Content-Type": "application/zip"},
        )
    assert second.status_code == 409


async def test_import_without_skill_md_returns_400(isolated_skill_registry: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "no skill")

    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/skills/import?filename=bad.zip",
            content=buffer.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
    assert response.status_code == 400


async def test_patch_enable_toggles_and_filters_capabilities(
    isolated_skill_registry: Path,
) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        disable = await client.patch("/api/skills/seeded", json={"enabled": False})
        assert disable.status_code == 200
        assert disable.json()["enabled"] is False

        caps = await client.get("/api/capabilities")
        assert "seeded" not in {s["name"] for s in caps.json()["skills"]}

        enable = await client.patch("/api/skills/seeded", json={"enabled": True})
        assert enable.json()["enabled"] is True
        caps2 = await client.get("/api/capabilities")
        assert "seeded" in {s["name"] for s in caps2.json()["skills"]}


async def test_patch_unknown_skill_returns_404(isolated_skill_registry: Path) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch("/api/skills/ghost", json={"enabled": False})
    assert response.status_code == 404


async def test_delete_builtin_returns_403_imported_ok(isolated_skill_registry: Path) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        protected = await client.delete("/api/skills/seeded")
        assert protected.status_code == 403

        await client.post(
            "/api/skills/import?filename=temp.zip",
            content=_skill_zip("temp"),
            headers={"Content-Type": "application/zip"},
        )
        removed = await client.delete("/api/skills/temp")
        assert removed.status_code == 200
        listed = await client.get("/api/skills")
        assert "temp" not in {s["name"] for s in listed.json()["skills"]}


async def test_skill_detail_returns_body(isolated_skill_registry: Path) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/skills/seeded")
    assert response.status_code == 200
    assert "Body" in response.json()["body"]


# -- Skill marketplace API ---------------------------------------------------


from agentengine.skills.catalog import LocalCatalogProvider, SkillCatalog


@pytest.fixture
def isolated_skill_market(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``web_api.SKILL_REGISTRY`` and ``SKILL_CATALOG`` at temp storage."""
    skills_dir = tmp_path / ".agent" / "skills"
    skills_dir.mkdir(parents=True)
    catalog_root = tmp_path / "catalog_data"
    catalog_root.mkdir()
    (catalog_root / "catalog.json").write_text(
        json.dumps(
            [
                {
                    "id": "summarize",
                    "name": "Summarize",
                    "description": "Summarize text",
                    "category": "效率提升",
                    "downloads": 100,
                    "rating": 4.5,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    entry_dir = catalog_root / "summarize"
    entry_dir.mkdir()
    (entry_dir / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize text\n---\nBody",
        encoding="utf-8",
    )
    from agentengine.skills.registry import SkillRegistry

    registry = SkillRegistry(cwd=tmp_path, db_path=tmp_path / "meta.db")
    catalog = SkillCatalog(provider=LocalCatalogProvider(root=catalog_root), registry=registry)
    monkeypatch.setattr(web_api, "SKILL_REGISTRY", registry)
    monkeypatch.setattr(web_api, "SKILL_CATALOG", catalog)


async def test_market_lists_entries(isolated_skill_market: None) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/skill-market")
    assert response.status_code == 200
    skills = response.json()["skills"]
    summarize = next(s for s in skills if s["id"] == "summarize")
    assert summarize["installed"] is False
    assert summarize["category"] == "效率提升"
    assert summarize["rating"] == 4.5


async def test_market_categories(isolated_skill_market: None) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/skill-market/categories")
    assert response.status_code == 200
    categories = response.json()["categories"]
    assert categories[0] == "全部"
    assert "效率提升" in categories


async def test_market_install_then_visible_in_skills(isolated_skill_market: None) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        install = await client.post("/api/skill-market/summarize/install")
        assert install.status_code == 200
        assert install.json()["source"] == "market"

        # Now flagged installed in the market listing...
        market = await client.get("/api/skill-market")
        summarize = next(s for s in market.json()["skills"] if s["id"] == "summarize")
        assert summarize["installed"] is True

        # ...and present in the installed-skills endpoint.
        installed = await client.get("/api/skills")
        assert "summarize" in {s["name"] for s in installed.json()["skills"]}


async def test_market_install_twice_returns_409(isolated_skill_market: None) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post("/api/skill-market/summarize/install")
        assert first.status_code == 200
        second = await client.post("/api/skill-market/summarize/install")
    assert second.status_code == 409


async def test_market_install_unknown_returns_400(isolated_skill_market: None) -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/skill-market/ghost/install")
    assert response.status_code == 400


# -- Experts API -------------------------------------------------------------


async def test_experts_list_returns_runnable_experts() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/experts")
    assert response.status_code == 200
    experts = response.json()["experts"]
    assert len(experts) >= 6
    names = {e["name"] for e in experts}
    # Base agents (no ``role``) are not experts.
    assert "general_chat" not in names
    sample = experts[0]
    assert {"name", "role", "description", "category", "skills"} <= sample.keys()


async def test_experts_categories() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/experts/categories")
    assert response.status_code == 200
    categories = response.json()["categories"]
    assert categories[0] == "全部"
    assert len(categories) > 1


async def test_experts_scenarios() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/experts/scenarios")
    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert scenarios  # at least one featured scenario
    first = scenarios[0]
    assert "scenario" in first
    assert isinstance(first["experts"], list)


async def test_expert_is_runnable_via_run_stream() -> None:
    """A selected expert's name is a real agent the run-stream can execute."""
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=10.0) as client:
        experts = (await client.get("/api/experts")).json()["experts"]
        expert_name = experts[0]["name"]
        async with client.stream(
            "GET",
            "/api/runs/stream",
            params={
                "query": "你好",
                "agent_name": expert_name,
                "conversation_id": "expert-run-test",
            },
        ) as response:
            assert response.status_code == 200
            events = [evt async for evt in _consume_sse(response)]

    types = [e["event"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "done"


# -- Expert teams API --------------------------------------------------------


async def test_expert_teams_list() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/expert-teams")
    assert response.status_code == 200
    teams = response.json()["teams"]
    assert len(teams) >= 1
    team = teams[0]
    assert {"id", "name", "members", "member_experts"} <= team.keys()
    assert len(team["members"]) >= 1
    # member_experts resolve to full expert views
    assert len(team["member_experts"]) == len(team["members"])


async def test_expert_team_members_are_runnable_agents() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        teams = (await client.get("/api/expert-teams")).json()["teams"]
        experts = (await client.get("/api/experts")).json()["experts"]
    expert_names = {e["name"] for e in experts}
    for team in teams:
        for member in team["members"]:
            assert member in expert_names


async def test_expert_team_categories() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/expert-teams/categories")
    assert response.status_code == 200
    categories = response.json()["categories"]
    assert categories[0] == "全部"


async def test_expert_team_detail_and_404() -> None:
    transport = httpx.ASGITransport(app=web_api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        teams = (await client.get("/api/expert-teams")).json()["teams"]
        team_id = teams[0]["id"]
        ok = await client.get(f"/api/expert-teams/{team_id}")
        assert ok.status_code == 200
        assert ok.json()["id"] == team_id
        missing = await client.get("/api/expert-teams/does-not-exist")
        assert missing.status_code == 404
