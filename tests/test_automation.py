"""Tests for the automation store, scheduler, and web endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.backend.services import web_api
from app.backend.services.automation import (
    Automation,
    AutomationScheduler,
    AutomationStore,
)


def _automation(**overrides: Any) -> Automation:
    fields: dict[str, Any] = {
        "id": "",
        "name": "nightly",
        "prompt": "summarize the logs",
        "agent_name": "general_chat",
        "cron": "0 * * * *",
    }
    fields.update(overrides)
    return Automation(**fields)


def _past_iso(minutes: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _future_iso(minutes: int = 5) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# -- AutomationStore -----------------------------------------------------------


def test_store_add_get_list_delete(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())

    assert added.id
    assert added.created_at
    assert added.next_run_at  # computed from cron
    assert store.get(added.id) is not None
    assert [a.id for a in store.list_all()] == [added.id]

    assert store.delete(added.id) is True
    assert store.list_all() == []
    assert store.delete(added.id) is False


def test_store_rejects_invalid_cron(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    with pytest.raises(ValueError):
        store.add(_automation(cron="not-a-cron"))

    added = store.add(_automation())
    with pytest.raises(ValueError):
        store.update(added.id, {"cron": "99 99 99 99 99"})


def test_store_update_recomputes_next_run_on_cron_change(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())
    store.update(added.id, {"next_run_at": "2000-01-01T00:00:00+00:00"})

    updated = store.update(added.id, {"cron": "*/5 * * * *", "name": "renamed"})

    assert updated is not None
    assert updated.name == "renamed"
    assert updated.cron == "*/5 * * * *"
    # cron changed without an explicit next_run_at: recomputed into the future.
    assert updated.next_run_at != "2000-01-01T00:00:00+00:00"
    assert datetime.fromisoformat(updated.next_run_at) > datetime.now(timezone.utc)


def test_store_record_run_keeps_last_20_and_updates_status(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())

    for i in range(25):
        store.record_run(
            added.id,
            started_at=f"2026-01-01T00:{i:02d}:00+00:00",
            status="success" if i % 2 == 0 else "failed",
            summary=f"run {i}",
            error="" if i % 2 == 0 else f"err {i}",
        )

    runs = store.list_runs(added.id)
    assert len(runs) == 20
    # Newest first.
    assert runs[0]["summary"] == "run 24"
    assert runs[-1]["summary"] == "run 5"

    refreshed = store.get(added.id)
    assert refreshed is not None
    assert refreshed.last_status == "success"
    assert refreshed.last_error == ""
    assert refreshed.last_run_at == "2026-01-01T00:24:00+00:00"


def test_store_delete_removes_run_history(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())
    store.record_run(
        added.id, started_at=_past_iso(), status="success", summary="s", error=""
    )
    assert store.delete(added.id) is True
    assert store.list_runs(added.id) == []


# -- AutomationScheduler -------------------------------------------------------


async def test_tick_runs_due_automation_and_advances_schedule(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())
    store.update(added.id, {"next_run_at": _past_iso()})

    calls: list[dict[str, Any]] = []

    async def fake_run_events(*, query: str, agent_name: str, conversation_id: str) -> str:
        calls.append(
            {"query": query, "agent_name": agent_name, "conversation_id": conversation_id}
        )
        return "run summary"

    scheduler = AutomationScheduler(store=store, run_events=fake_run_events)
    await scheduler.tick()

    assert calls == [
        {
            "query": "summarize the logs",
            "agent_name": "general_chat",
            "conversation_id": f"automation-{added.id}",
        }
    ]
    refreshed = store.get(added.id)
    assert refreshed is not None
    assert refreshed.last_status == "success"
    assert refreshed.last_error == ""
    # Schedule advanced into the future.
    assert datetime.fromisoformat(refreshed.next_run_at) > datetime.now(timezone.utc)
    runs = store.list_runs(added.id)
    assert len(runs) == 1
    assert runs[0]["summary"] == "run summary"
    assert runs[0]["status"] == "success"


async def test_tick_skips_future_and_disabled_automations(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    future = store.add(_automation(name="future"))
    store.update(future.id, {"next_run_at": _future_iso()})
    disabled = store.add(_automation(name="disabled", enabled=False))
    store.update(disabled.id, {"next_run_at": _past_iso()})

    calls: list[str] = []

    async def fake_run_events(**kwargs: Any) -> str:
        calls.append(kwargs["query"])
        return "x"

    scheduler = AutomationScheduler(store=store, run_events=fake_run_events)
    await scheduler.tick()

    assert calls == []


async def test_tick_records_failures_without_dying(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())
    store.update(added.id, {"next_run_at": _past_iso()})

    async def failing_run_events(**kwargs: Any) -> str:
        raise RuntimeError("agent blew up")

    scheduler = AutomationScheduler(store=store, run_events=failing_run_events)
    await scheduler.tick()

    refreshed = store.get(added.id)
    assert refreshed is not None
    assert refreshed.last_status == "failed"
    assert "agent blew up" in refreshed.last_error
    # Even on failure the schedule advances so we don't hot-loop.
    assert datetime.fromisoformat(refreshed.next_run_at) > datetime.now(timezone.utc)


async def test_run_timeout_marks_run_failed(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())
    store.update(added.id, {"next_run_at": _past_iso()})

    async def slow_run_events(**kwargs: Any) -> str:
        await asyncio.sleep(30)
        return "never"

    scheduler = AutomationScheduler(
        store=store, run_events=slow_run_events, run_timeout_seconds=0.05
    )
    await scheduler.tick()

    refreshed = store.get(added.id)
    assert refreshed is not None
    assert refreshed.last_status == "failed"
    assert "TimeoutError" in refreshed.last_error


async def test_trigger_queues_immediate_run(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")
    added = store.add(_automation())
    store.update(added.id, {"next_run_at": _future_iso(60)})

    ran: list[str] = []

    async def fake_run_events(*, query: str, **kwargs: Any) -> str:
        ran.append(query)
        return "done"

    scheduler = AutomationScheduler(store=store, run_events=fake_run_events)
    assert scheduler.trigger(added.id) is True
    assert scheduler.trigger("missing") is False

    for _ in range(50):
        if ran:
            break
        await asyncio.sleep(0.02)
    assert ran == ["summarize the logs"]


async def test_stop_without_start_is_noop(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automations.json")

    async def fake_run_events(**kwargs: Any) -> str:
        return ""

    scheduler = AutomationScheduler(store=store, run_events=fake_run_events)
    scheduler.start()
    await scheduler.stop()
    # Stopping twice is fine too.
    await scheduler.stop()


# -- Web endpoints --------------------------------------------------------------


@pytest.fixture
def isolated_automation_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AutomationStore:
    store = AutomationStore(tmp_path / "automations.json")
    monkeypatch.setattr(web_api, "AUTOMATION_STORE", store)
    return store


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web_api.app), base_url="http://testserver"
    )


async def test_automation_crud_endpoints(isolated_automation_store: AutomationStore) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/automations",
            json={
                "name": "daily digest",
                "prompt": "give me a digest",
                "agent_name": "general_chat",
                "cron": "0 9 * * *",
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["name"] == "daily digest"
        assert body["enabled"] is True
        assert body["next_run_at"]
        automation_id = body["id"]

        listed = await client.get("/api/automations")
        assert [a["id"] for a in listed.json()["automations"]] == [automation_id]

        patched = await client.patch(
            f"/api/automations/{automation_id}", json={"enabled": False, "cron": "0 10 * * *"}
        )
        assert patched.status_code == 200
        assert patched.json()["enabled"] is False
        assert patched.json()["cron"] == "0 10 * * *"

        deleted = await client.delete(f"/api/automations/{automation_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert (await client.get("/api/automations")).json()["automations"] == []


async def test_automation_invalid_cron_returns_400(
    isolated_automation_store: AutomationStore,
) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/automations",
            json={"name": "x", "prompt": "p", "agent_name": "general_chat", "cron": "nope"},
        )
        assert created.status_code == 400

        ok = await client.post(
            "/api/automations",
            json={"name": "x", "prompt": "p", "agent_name": "general_chat", "cron": "0 * * * *"},
        )
        patched = await client.patch(
            f"/api/automations/{ok.json()['id']}", json={"cron": "nope"}
        )
        assert patched.status_code == 400


async def test_automation_unknown_id_returns_404(
    isolated_automation_store: AutomationStore,
) -> None:
    async with _client() as client:
        assert (await client.patch("/api/automations/ghost", json={"name": "x"})).status_code == 404
        assert (await client.delete("/api/automations/ghost")).status_code == 404
        assert (await client.post("/api/automations/ghost/run")).status_code == 404
        assert (await client.get("/api/automations/ghost/runs")).status_code == 404


async def test_automation_run_now_and_runs_history(
    isolated_automation_store: AutomationStore,
) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/automations",
            json={
                "name": "on demand",
                "prompt": "do it now",
                "agent_name": "general_chat",
                "cron": "0 9 * * *",
            },
        )
        automation_id = created.json()["id"]

        ran: list[str] = []

        async def fake_run_events(*, query: str, **kwargs: Any) -> str:
            ran.append(query)
            return "manual run summary"

        scheduler = AutomationScheduler(
            store=isolated_automation_store, run_events=fake_run_events
        )
        web_api.app.state.automation_scheduler = scheduler
        try:
            queued = await client.post(f"/api/automations/{automation_id}/run")
            assert queued.status_code == 200
            assert queued.json() == {"queued": True}

            for _ in range(50):
                if ran:
                    break
                await asyncio.sleep(0.02)
        finally:
            web_api.app.state.automation_scheduler = None

        assert ran == ["do it now"]

        runs = await client.get(f"/api/automations/{automation_id}/runs")
        assert runs.status_code == 200
        history = runs.json()["runs"]
        assert len(history) == 1
        assert history[0]["status"] == "success"
        assert history[0]["summary"] == "manual run summary"
