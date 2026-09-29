"""Scheduled (cron) agent runs with JSON persistence.

Automations let the backend run an agent prompt on a cron schedule without a
user watching the stream. The store mirrors the ``McpConnectorStore`` pattern:
one JSON file holding both the automation configs and a bounded run history.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

MAX_RUN_HISTORY = 20
DEFAULT_TICK_SECONDS = 30.0
DEFAULT_RUN_TIMEOUT_SECONDS = 600.0

# Signature of the injectable agent runner used by the scheduler.
RunEventsFn = Callable[..., Awaitable[str]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_run_iso(cron: str, start: datetime | None = None) -> str:
    """Compute the next fire time for ``cron`` as an ISO-8601 UTC string."""
    base = start or _utc_now()
    nxt: datetime = croniter(cron, base).get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt.astimezone(timezone.utc).isoformat()


def _validate_cron(cron: str) -> None:
    try:
        _next_run_iso(cron)
    except Exception as exc:
        raise ValueError(f"invalid cron expression: {cron!r}") from exc


@dataclass(slots=True)
class Automation:
    id: str
    name: str
    prompt: str
    agent_name: str
    cron: str
    enabled: bool = True
    created_at: str = ""
    last_run_at: str = ""
    next_run_at: str = ""
    last_status: str = ""
    last_error: str = ""

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class AutomationStore:
    """JSON-file-backed CRUD store for automations and their run history."""

    def __init__(self, file_path: Path | str) -> None:
        self._path = Path(file_path)

    # -- read / write -----------------------------------------------------

    def _load(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"automations": [], "runs": {}}
        if not self._path.is_file():
            return empty
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("automations_read_error")
            return empty
        if not isinstance(data, dict):
            return empty
        if not isinstance(data.get("automations"), list):
            data["automations"] = []
        if not isinstance(data.get("runs"), dict):
            data["runs"] = {}
        return data

    def _save(
        self,
        automations: list[Automation],
        runs: dict[str, list[dict[str, Any]]],
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "automations": [a.snapshot() for a in automations],
            "runs": runs,
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- automations CRUD --------------------------------------------------

    def list_all(self) -> list[Automation]:
        rows = self._load()["automations"]
        return [_row_to_automation(r) for r in rows]

    def get(self, automation_id: str) -> Automation | None:
        for automation in self.list_all():
            if automation.id == automation_id:
                return automation
        return None

    def add(self, automation: Automation) -> Automation:
        _validate_cron(automation.cron)
        if not automation.id:
            automation.id = uuid.uuid4().hex[:8]
        if not automation.created_at:
            automation.created_at = _utc_now().isoformat()
        if not automation.next_run_at:
            automation.next_run_at = _next_run_iso(automation.cron)
        data = self._load()
        automations = self.list_all()
        automations.append(automation)
        self._save(automations, data["runs"])
        logger.info(
            "automation_added id=%s name=%s cron=%s",
            automation.id,
            automation.name,
            automation.cron,
        )
        return automation

    def update(self, automation_id: str, patch: dict[str, Any]) -> Automation | None:
        if "cron" in patch:
            _validate_cron(str(patch["cron"]))
        data = self._load()
        automations = self.list_all()
        for automation in automations:
            if automation.id == automation_id:
                for key in (
                    "name",
                    "prompt",
                    "agent_name",
                    "cron",
                    "enabled",
                    "next_run_at",
                    "last_run_at",
                    "last_status",
                    "last_error",
                ):
                    if key in patch:
                        setattr(automation, key, patch[key])
                if "cron" in patch and "next_run_at" not in patch:
                    automation.next_run_at = _next_run_iso(automation.cron)
                self._save(automations, data["runs"])
                logger.info("automation_updated id=%s", automation_id)
                return automation
        return None

    def delete(self, automation_id: str) -> bool:
        data = self._load()
        automations = self.list_all()
        filtered = [a for a in automations if a.id != automation_id]
        if len(filtered) == len(automations):
            return False
        runs = data["runs"]
        runs.pop(automation_id, None)
        self._save(filtered, runs)
        logger.info("automation_deleted id=%s", automation_id)
        return True

    # -- run history --------------------------------------------------------

    def record_run(
        self,
        automation_id: str,
        *,
        started_at: str,
        status: str,
        summary: str,
        error: str,
    ) -> None:
        data = self._load()
        automations = self.list_all()
        runs: dict[str, list[dict[str, Any]]] = data["runs"]
        history = runs.setdefault(automation_id, [])
        history.append(
            {
                "automation_id": automation_id,
                "started_at": started_at,
                "status": status,
                "summary": summary,
                "error": error,
            }
        )
        del history[:-MAX_RUN_HISTORY]
        for automation in automations:
            if automation.id == automation_id:
                automation.last_run_at = started_at
                automation.last_status = status
                automation.last_error = error
        self._save(automations, runs)

    def list_runs(self, automation_id: str) -> list[dict[str, Any]]:
        data = self._load()
        runs = data["runs"].get(automation_id, [])
        return list(reversed(runs))


def _row_to_automation(row: dict[str, Any]) -> Automation:
    return Automation(
        id=str(row.get("id", "")),
        name=str(row.get("name", "")),
        prompt=str(row.get("prompt", "")),
        agent_name=str(row.get("agent_name", "general_chat")),
        cron=str(row.get("cron", "")),
        enabled=bool(row.get("enabled", True)),
        created_at=str(row.get("created_at", "")),
        last_run_at=str(row.get("last_run_at", "")),
        next_run_at=str(row.get("next_run_at", "")),
        last_status=str(row.get("last_status", "")),
        last_error=str(row.get("last_error", "")),
    )


class AutomationScheduler:
    """Periodic loop that fires due automations one at a time.

    The actual agent run is delegated to an injected ``run_events`` coroutine
    so tests can substitute a fake and the web layer can bind its own sandbox.
    """

    def __init__(
        self,
        store: AutomationStore,
        run_events: RunEventsFn,
        *,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        run_timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> None:
        self._store = store
        self._run_events = run_events
        self._tick_seconds = tick_seconds
        self._run_timeout_seconds = run_timeout_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def trigger(self, automation_id: str) -> bool:
        """Queue an immediate background run, ignoring the cron schedule."""
        automation = self._store.get(automation_id)
        if automation is None:
            return False
        asyncio.create_task(self._run_one(automation))
        return True

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("automation_tick_error")
            await asyncio.sleep(self._tick_seconds)

    async def tick(self) -> None:
        """Run every due automation once, serially."""
        now = _utc_now()
        for automation in self._store.list_all():
            if not automation.enabled or not automation.next_run_at:
                continue
            try:
                due = datetime.fromisoformat(automation.next_run_at)
            except ValueError:
                logger.warning(
                    "automation_bad_next_run id=%s value=%s",
                    automation.id,
                    automation.next_run_at,
                )
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due <= now:
                await self._run_one(automation)

    async def _run_one(self, automation: Automation) -> None:
        started_at = _utc_now()
        status = "success"
        summary = ""
        error = ""
        try:
            summary = await asyncio.wait_for(
                self._run_events(
                    query=automation.prompt,
                    agent_name=automation.agent_name,
                    conversation_id=f"automation-{automation.id}",
                ),
                timeout=self._run_timeout_seconds,
            )
        except Exception as exc:
            status = "failed"
            error = str(exc) or type(exc).__name__
            logger.exception("automation_run_failed id=%s", automation.id)
        try:
            self._store.record_run(
                automation.id,
                started_at=started_at.isoformat(),
                status=status,
                summary=summary,
                error=error,
            )
            self._store.update(
                automation.id,
                {"next_run_at": _next_run_iso(automation.cron, _utc_now())},
            )
        except Exception:
            logger.exception("automation_record_failed id=%s", automation.id)
