"""SQLite implementation of ``PersistencePort``.

Goals:
- Zero external dependencies (uses stdlib ``sqlite3`` + ``asyncio.to_thread``).
- Single-file storage — convenient for chatbot MVPs and dev/test.
- Schema covers the four PersistencePort methods: messages, runs, artifacts.
- Concurrent access from multiple coroutines is safe because every operation
  opens its own connection in ``check_same_thread=False`` mode and SQLite
  serializes writes at the file level.

For multi-replica production deployments, swap this for a Postgres
implementation behind the same ``PersistencePort`` protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    agent_name      TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    position        INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    UNIQUE (conversation_id, position)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, position);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    conversation_id TEXT,
    agent_name      TEXT,
    input_msg       TEXT NOT NULL,
    reply_msg       TEXT NOT NULL,
    metadata        TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_conversation
    ON runs (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    data            TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run
    ON artifacts (run_id);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class SqlitePersistence:
    """SQLite-backed implementation of ``PersistencePort``.

    Each public method delegates the blocking sqlite3 call to a worker thread
    via ``asyncio.to_thread`` so the event loop stays responsive.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(Path(db_path))
        self._init_lock = asyncio.Lock()
        self._initialised = False

    async def _ensure_schema(self) -> None:
        if self._initialised:
            return
        async with self._init_lock:
            if self._initialised:
                return
            await asyncio.to_thread(self._init_schema_sync)
            self._initialised = True

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        logger.info("sqlite_persistence_initialised path=%s", self._db_path)

    def _connect(self) -> sqlite3.Connection:
        # check_same_thread=False is safe because we serialize per-call via
        # to_thread; SQLite itself handles concurrent file-level locking.
        conn = sqlite3.connect(
            self._db_path,
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage tx with BEGIN/COMMIT
        )
        conn.row_factory = sqlite3.Row
        # WAL gives us better concurrent-read behaviour without losing safety.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- PersistencePort methods ------------------------------------------

    async def save_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        agent_name: str,
        input_msg: str,
        reply_msg: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._ensure_schema()
        await asyncio.to_thread(
            self._save_run_sync,
            run_id,
            conversation_id,
            agent_name,
            input_msg,
            reply_msg,
            json.dumps(metadata or {}, ensure_ascii=False),
        )

    def _save_run_sync(
        self,
        run_id: str,
        conversation_id: str,
        agent_name: str,
        input_msg: str,
        reply_msg: str,
        metadata_json: str,
    ) -> None:
        now = _now_ms()
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runs
                        (run_id, conversation_id, agent_name, input_msg, reply_msg, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, conversation_id, agent_name, input_msg, reply_msg, metadata_json, now),
                )
                if conversation_id:
                    conn.execute(
                        """
                        INSERT INTO conversations (conversation_id, agent_name, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(conversation_id) DO UPDATE SET
                            agent_name = excluded.agent_name,
                            updated_at = excluded.updated_at
                        """,
                        (conversation_id, agent_name, now, now),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    async def save_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        if not conversation_id:
            return
        await self._ensure_schema()
        await asyncio.to_thread(self._save_messages_sync, conversation_id, messages)

    def _save_messages_sync(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Replace the conversation's messages atomically.

        We delete then re-insert: simplest semantics, no risk of duplicates,
        and the caller (Memory.save_to_db) always passes the full list. For
        very long conversations this becomes wasteful — switch to incremental
        append via ``position`` watermarks if profiling demands it.

        The ``messages`` list is expected to contain the full persistent
        payload produced by ``Message.to_persistent()`` so that metadata is
        preserved across restarts.
        """
        now = _now_ms()
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "INSERT INTO conversations (conversation_id, created_at, updated_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET updated_at = excluded.updated_at",
                    (conversation_id, now, now),
                )
                conn.execute(
                    "DELETE FROM messages WHERE conversation_id = ?",
                    (conversation_id,),
                )
                rows = [
                    (
                        conversation_id,
                        index,
                        str(msg.get("role", "")),
                        _flatten_content(msg.get("content", "")),
                        json.dumps(msg, ensure_ascii=False),
                        now,
                    )
                    for index, msg in enumerate(messages)
                ]
                if rows:
                    conn.executemany(
                        """
                        INSERT INTO messages
                            (conversation_id, position, role, content, payload, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    async def load_messages(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        if not conversation_id:
            return []
        await self._ensure_schema()
        return await asyncio.to_thread(self._load_messages_sync, conversation_id)

    def _load_messages_sync(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT payload FROM messages WHERE conversation_id = ? ORDER BY position ASC",
                (conversation_id,),
            )
            rows = cursor.fetchall()
        return [json.loads(row["payload"]) for row in rows]

    async def save_artifact(
        self,
        run_id: str,
        artifact_type: str,
        data: dict[str, Any],
    ) -> None:
        await self._ensure_schema()
        await asyncio.to_thread(
            self._save_artifact_sync,
            run_id,
            artifact_type,
            json.dumps(data, ensure_ascii=False),
        )

    def _save_artifact_sync(
        self,
        run_id: str,
        artifact_type: str,
        data_json: str,
    ) -> None:
        now = _now_ms()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (run_id, artifact_type, data, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, artifact_type, data_json, now),
            )

    # -- Convenience helpers (not part of PersistencePort) ----------------

    async def list_runs(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return run records for a conversation, newest first."""
        await self._ensure_schema()
        return await asyncio.to_thread(self._list_runs_sync, conversation_id, limit)

    def _list_runs_sync(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT run_id, conversation_id, agent_name, input_msg, reply_msg, metadata, created_at
                FROM runs
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            rows = cursor.fetchall()
        return [
            {
                "run_id": row["run_id"],
                "conversation_id": row["conversation_id"],
                "agent_name": row["agent_name"],
                "input_msg": row["input_msg"],
                "reply_msg": row["reply_msg"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def _flatten_content(content: Any) -> str:
    """Reduce multimodal content lists to plain text for the indexed column.

    The full structure is preserved in `payload` (JSON); this column exists
    so future text-search / debugging stays simple without joining JSON.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content) if content is not None else ""
