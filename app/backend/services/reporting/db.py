"""SQLite metadata store for report files.

Schema
------
blobs(sha256 PK, size, mime, parsed_json, parser_version, created_at)
    one row per unique byte stream. ``parsed_json`` caches the
    ParsedFile payload so the same content is never parsed twice.

file_refs(file_id PK, sha256 FK, tenant_id, conversation_id, filename,
          created_at, deleted_at)
    one row per upload. Multiple file_refs may point at the same
    blob; deduplication happens at the blob layer while ownership
    and conversation scoping live here.

reports(report_id PK, conversation_id, title, intent, skill, status,
        html_file_path, html_size, error, created_at, updated_at)
    one row per generated HTML report. The HTML body lives on disk
    under ``<upload_root>/reports/<report_id>.html``; this table
    stores metadata + a relative file path so reports survive
    process restarts and can be listed / resumed across sessions.

Connections are opened per operation rather than cached, because
``aiosqlite`` spawns a worker thread bound to the creating event loop —
caching a connection breaks the test suite where each test runs on a
fresh loop, and breaks reload-on-edit servers for the same reason.
SQLite handles a fresh connection per query cheaply at this footprint.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite


PARSER_VERSION = "v2"


@dataclass(slots=True)
class BlobRow:
    sha256: str
    size: int
    mime: str
    parsed_json: str
    parser_version: str
    created_at: str

    def parsed(self) -> dict[str, Any]:
        return json.loads(self.parsed_json)


@dataclass(slots=True)
class FileRefRow:
    file_id: str
    sha256: str
    tenant_id: str
    conversation_id: str
    filename: str
    created_at: str
    deleted_at: str | None


@dataclass(slots=True)
class ReportRow:
    report_id: str
    conversation_id: str
    title: str
    intent: str
    skill: str
    status: str
    html_file_path: str
    html_size: int
    error: str
    created_at: str
    updated_at: str


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportMetadataDB:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[aiosqlite.Connection]:
        await self._ensure_initialized()
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            await conn.close()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(self._path)
            try:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS blobs (
                        sha256 TEXT PRIMARY KEY,
                        size INTEGER NOT NULL,
                        mime TEXT NOT NULL,
                        parsed_json TEXT NOT NULL,
                        parser_version TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS file_refs (
                        file_id TEXT PRIMARY KEY,
                        sha256 TEXT NOT NULL REFERENCES blobs(sha256),
                        tenant_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        deleted_at TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_file_refs_conv
                        ON file_refs(conversation_id, deleted_at);
                    CREATE INDEX IF NOT EXISTS idx_file_refs_tenant
                        ON file_refs(tenant_id, deleted_at);

                    CREATE TABLE IF NOT EXISTS reports (
                        report_id       TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        title           TEXT NOT NULL,
                        intent          TEXT DEFAULT '',
                        skill           TEXT DEFAULT 'data_analysis',
                        status          TEXT NOT NULL DEFAULT 'created',
                        html_file_path  TEXT DEFAULT '',
                        html_size       INTEGER DEFAULT 0,
                        error           TEXT DEFAULT '',
                        created_at      TEXT NOT NULL,
                        updated_at      TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_reports_conv
                        ON reports(conversation_id, updated_at);
                    CREATE INDEX IF NOT EXISTS idx_reports_status
                        ON reports(conversation_id, status);
                    """
                )
                await conn.commit()
            finally:
                await conn.close()
            self._initialized = True

    async def close(self) -> None:
        # Kept for API symmetry with the previous cached-connection
        # implementation; nothing to release now.
        return None

    async def get_blob(self, sha256: str) -> BlobRow | None:
        async with self._open() as conn:
            async with conn.execute(
                "SELECT sha256, size, mime, parsed_json, parser_version, created_at "
                "FROM blobs WHERE sha256 = ?",
                (sha256,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return BlobRow(
            sha256=row["sha256"],
            size=row["size"],
            mime=row["mime"],
            parsed_json=row["parsed_json"],
            parser_version=row["parser_version"],
            created_at=row["created_at"],
        )

    async def insert_blob(
        self,
        *,
        sha256: str,
        size: int,
        mime: str,
        parsed: dict[str, Any],
        parser_version: str = PARSER_VERSION,
    ) -> BlobRow:
        parsed_json = json.dumps(parsed, ensure_ascii=False)
        created_at = _utc_iso()
        async with self._open() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO blobs (sha256, size, mime, parsed_json, parser_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sha256, size, mime, parsed_json, parser_version, created_at),
            )
            await conn.commit()
        return BlobRow(
            sha256=sha256,
            size=size,
            mime=mime,
            parsed_json=parsed_json,
            parser_version=parser_version,
            created_at=created_at,
        )

    async def insert_file_ref(
        self,
        *,
        file_id: str,
        sha256: str,
        tenant_id: str,
        conversation_id: str,
        filename: str,
    ) -> FileRefRow:
        created_at = _utc_iso()
        async with self._open() as conn:
            await conn.execute(
                "INSERT INTO file_refs (file_id, sha256, tenant_id, conversation_id, filename, created_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (file_id, sha256, tenant_id, conversation_id, filename, created_at),
            )
            await conn.commit()
        return FileRefRow(
            file_id=file_id,
            sha256=sha256,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            filename=filename,
            created_at=created_at,
            deleted_at=None,
        )

    async def get_file_ref(self, file_id: str) -> FileRefRow | None:
        async with self._open() as conn:
            async with conn.execute(
                "SELECT file_id, sha256, tenant_id, conversation_id, filename, created_at, deleted_at "
                "FROM file_refs WHERE file_id = ? AND deleted_at IS NULL",
                (file_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_file_ref(row)

    async def list_file_refs_by_conversation(self, conversation_id: str) -> list[FileRefRow]:
        async with self._open() as conn:
            async with conn.execute(
                "SELECT file_id, sha256, tenant_id, conversation_id, filename, created_at, deleted_at "
                "FROM file_refs WHERE conversation_id = ? AND deleted_at IS NULL "
                "ORDER BY created_at ASC",
                (conversation_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_file_ref(row) for row in rows]

    # -- Reports ----------------------------------------------------------

    async def insert_report(
        self,
        *,
        report_id: str,
        conversation_id: str,
        title: str,
        intent: str = "",
        skill: str = "data_analysis",
    ) -> ReportRow:
        now = _utc_iso()
        async with self._open() as conn:
            await conn.execute(
                """
                INSERT INTO reports
                    (report_id, conversation_id, title, intent, skill, status,
                     html_file_path, html_size, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'created', '', 0, '', ?, ?)
                """,
                (report_id, conversation_id, title, intent, skill, now, now),
            )
            await conn.commit()
        return ReportRow(
            report_id=report_id,
            conversation_id=conversation_id,
            title=title,
            intent=intent,
            skill=skill,
            status="created",
            html_file_path="",
            html_size=0,
            error="",
            created_at=now,
            updated_at=now,
        )

    async def update_report(
        self,
        report_id: str,
        *,
        status: str | None = None,
        html_file_path: str | None = None,
        html_size: int | None = None,
        error: str | None = None,
    ) -> ReportRow | None:
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [_utc_iso()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if html_file_path is not None:
            sets.append("html_file_path = ?")
            params.append(html_file_path)
        if html_size is not None:
            sets.append("html_size = ?")
            params.append(html_size)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(report_id)

        async with self._open() as conn:
            await conn.execute(
                f"UPDATE reports SET {', '.join(sets)} WHERE report_id = ?",
                params,
            )
            await conn.commit()
            async with conn.execute(
                "SELECT report_id, conversation_id, title, intent, skill, status, "
                "html_file_path, html_size, error, created_at, updated_at "
                "FROM reports WHERE report_id = ?",
                (report_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_report(row)

    async def get_report(self, report_id: str) -> ReportRow | None:
        async with self._open() as conn:
            async with conn.execute(
                "SELECT report_id, conversation_id, title, intent, skill, status, "
                "html_file_path, html_size, error, created_at, updated_at "
                "FROM reports WHERE report_id = ?",
                (report_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_report(row)

    async def list_reports_by_conversation(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
    ) -> list[ReportRow]:
        async with self._open() as conn:
            async with conn.execute(
                "SELECT report_id, conversation_id, title, intent, skill, status, "
                "html_file_path, html_size, error, created_at, updated_at "
                "FROM reports WHERE conversation_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (conversation_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_report(row) for row in rows]


def _row_to_file_ref(row: aiosqlite.Row) -> FileRefRow:
    return FileRefRow(
        file_id=row["file_id"],
        sha256=row["sha256"],
        tenant_id=row["tenant_id"],
        conversation_id=row["conversation_id"],
        filename=row["filename"],
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
    )


def _row_to_report(row: aiosqlite.Row) -> ReportRow:
    return ReportRow(
        report_id=row["report_id"],
        conversation_id=row["conversation_id"],
        title=row["title"],
        intent=row["intent"],
        skill=row["skill"],
        status=row["status"],
        html_file_path=row["html_file_path"],
        html_size=row["html_size"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
