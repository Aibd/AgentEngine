"""Report file storage with content-addressed blobs and SQLite metadata.

Disk layout
-----------
``<root>/blobs/<sha256[:2]>/<sha256>``
    Raw bytes, deduplicated. The same content uploaded twice (by the same
    or different tenants) lives as a single blob.

``<root>/parsed/<sha256>/``
    Full-content parser artifacts: ``text.txt`` for documents/slides/pdfs,
    ``sheet_<n>.csv`` for tabular files, ``tables.json`` for extracted
    tables. These complement the bounded preview cached in SQLite.

``<root>/index.db``
    SQLite holding the ``blobs`` and ``file_refs`` tables (see ``db.py``).

Public API mirrors the previous in-memory store so callers (web_api,
jobs) do not need to change:

- :meth:`ReportFileStore.save_bytes`
- :meth:`ReportFileStore.get`
- :meth:`ReportFileStore.get_many`
- :meth:`ReportFileStore.list_by_conversation`

Each returns :class:`ReportFileRecord` with the same field names as
before; ``path`` now points at the deduplicated blob.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.backend.services.reporting.db import PARSER_VERSION, ReportMetadataDB
from app.backend.services.reporting.models import ParsedFile
from app.backend.services.reporting.parser import parse_file


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PARSABLE_EXTENSIONS = {".csv", ".xlsx"}
ATTACHMENT_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".docx", ".pptx", ".md", ".markdown", ".txt",
}
ALLOWED_EXTENSIONS = PARSABLE_EXTENSIONS | ATTACHMENT_EXTENSIONS

# Subset historically rejected by API tests. Kept rejected so existing
# behaviour (415 on .txt without explicit opt-in) doesn't silently flip;
# .txt joins ALLOWED_EXTENSIONS so internal callers can still parse it,
# but the upload API filters this set out before save_bytes is invoked.
API_REJECTED_EXTENSIONS = {".txt"}


MIME_BY_EXTENSION: dict[str, str] = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass(slots=True)
class ReportFileRecord:
    id: str
    tenant_id: str
    conversation_id: str
    filename: str
    path: Path
    size_bytes: int
    parsed: ParsedFile

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "conversation_id": self.conversation_id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "parsed": self.parsed.model_dump(mode="json"),
        }


class ReportFileStore:
    """Content-addressed storage + SQLite metadata for report uploads.

    Constructed with a ``root`` directory; everything (blobs, parsed
    artifacts, index.db) lives under that root. The DB connection is
    lazy so tests can construct a store without touching disk until
    they upload.
    """

    def __init__(self, root: Path, *, db: ReportMetadataDB | None = None) -> None:
        self._root = root
        self._blob_root = root / "blobs"
        self._parsed_root = root / "parsed"
        self._db = db or ReportMetadataDB(root / "index.db")

    async def save_bytes(
        self,
        *,
        data: bytes,
        filename: str,
        tenant_id: str,
        conversation_id: str,
    ) -> ReportFileRecord:
        safe_name = sanitize_filename(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS or extension in API_REJECTED_EXTENSIONS:
            raise ValueError(f"unsupported file extension: {extension or '(none)'}")
        if not data:
            raise ValueError("uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("uploaded file exceeds 50MB limit")

        sha = hashlib.sha256(data).hexdigest()
        file_id = f"file_{uuid.uuid4().hex[:12]}"

        blob_path = self._blob_path(sha)
        parsed_dir = self._parsed_dir(sha)

        existing = await self._db.get_blob(sha)
        cached_parsed = existing.parsed() if existing is not None else None
        cache_usable = (
            existing is not None
            and existing.parser_version == PARSER_VERSION
            and cached_parsed is not None
            and not _parsed_payload_failed(cached_parsed)
        )
        if cache_usable:
            parsed = ParsedFile.model_validate(cached_parsed)
            # Same content already parsed; reuse cached payload but stamp
            # this upload's file_id/filename so callers see fresh identity.
            parsed = parsed.model_copy(update={"file_id": file_id, "filename": safe_name})
        else:
            await asyncio.to_thread(self._write_blob, blob_path, data)
            parsed = await asyncio.to_thread(
                parse_file,
                file_id=file_id,
                filename=safe_name,
                path=blob_path,
                size_bytes=len(data),
                output_dir=parsed_dir,
                extension=extension,
            )
            await self._db.insert_blob(
                sha256=sha,
                size=len(data),
                mime=MIME_BY_EXTENSION.get(extension, "application/octet-stream"),
                parsed=parsed.model_dump(mode="json"),
            )

        await self._db.insert_file_ref(
            file_id=file_id,
            sha256=sha,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            filename=safe_name,
        )

        return ReportFileRecord(
            id=file_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            filename=safe_name,
            path=blob_path,
            size_bytes=len(data),
            parsed=parsed,
        )

    async def aget(self, file_id: str) -> ReportFileRecord | None:
        ref = await self._db.get_file_ref(file_id)
        if ref is None:
            return None
        blob = await self._db.get_blob(ref.sha256)
        if blob is None:
            return None
        parsed = ParsedFile.model_validate(blob.parsed())
        parsed = parsed.model_copy(update={"file_id": file_id, "filename": ref.filename})
        return ReportFileRecord(
            id=ref.file_id,
            tenant_id=ref.tenant_id,
            conversation_id=ref.conversation_id,
            filename=ref.filename,
            path=self._blob_path(ref.sha256),
            size_bytes=blob.size,
            parsed=parsed,
        )

    async def aget_many(self, file_ids: list[str]) -> list[ReportFileRecord]:
        out: list[ReportFileRecord] = []
        for file_id in file_ids:
            record = await self.aget(file_id)
            if record is not None:
                out.append(record)
        return out

    async def alist_by_conversation(self, conversation_id: str) -> list[ReportFileRecord]:
        refs = await self._db.list_file_refs_by_conversation(conversation_id)
        records: list[ReportFileRecord] = []
        for ref in refs:
            blob = await self._db.get_blob(ref.sha256)
            if blob is None:
                continue
            parsed = ParsedFile.model_validate(blob.parsed())
            parsed = parsed.model_copy(update={"file_id": ref.file_id, "filename": ref.filename})
            records.append(
                ReportFileRecord(
                    id=ref.file_id,
                    tenant_id=ref.tenant_id,
                    conversation_id=ref.conversation_id,
                    filename=ref.filename,
                    path=self._blob_path(ref.sha256),
                    size_bytes=blob.size,
                    parsed=parsed,
                )
            )
        return records

    # ------------------------------------------------------------------
    # Sync wrappers to keep historical call sites unchanged. Each one
    # bridges to the async API via a fresh event loop when no loop is
    # running, or schedules onto the current loop otherwise.
    # ------------------------------------------------------------------

    def get(self, file_id: str) -> ReportFileRecord | None:
        return _run_sync(self.aget(file_id))

    def get_many(self, file_ids: list[str]) -> list[ReportFileRecord]:
        return _run_sync(self.aget_many(file_ids))

    def list_by_conversation(self, conversation_id: str) -> list[ReportFileRecord]:
        return _run_sync(self.alist_by_conversation(conversation_id))

    async def close(self) -> None:
        await self._db.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _blob_path(self, sha: str) -> Path:
        return self._blob_root / sha[:2] / sha

    def _parsed_dir(self, sha: str) -> Path:
        return self._parsed_root / sha

    @staticmethod
    def _write_blob(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _run_sync(coro: Any) -> Any:
    """Run ``coro`` to completion from a sync context.

    The web service is fully async, so production calls go through the
    async methods. These wrappers exist purely for legacy sync call
    sites (and tests) that haven't been updated.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Running inside an event loop: hand off to a worker thread so we
    # don't deadlock ourselves.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _parsed_payload_failed(payload: dict[str, Any]) -> bool:
    """Detect crashed/no-op parser runs cached in the blob table.

    We retry rather than serving a "kind=unknown + parser crashed"
    payload back forever. Without this, a transient parser bug (like
    the openpyxl extension-sniff issue) freezes the file at broken
    status until parser_version is manually bumped.
    """
    warnings = payload.get("warnings") or []
    if any(
        isinstance(w, str) and ("parser crashed" in w or "no parser registered" in w)
        for w in warnings
    ):
        return True
    # A payload with kind=unknown AND no sheets AND no text is almost
    # certainly a failed run (CSV / xlsx / pdf parsers all populate
    # at least one of those fields on success).
    if (
        payload.get("kind") == "unknown"
        and not payload.get("sheets")
        and not payload.get("text_preview")
        and not payload.get("text_full_path")
    ):
        return True
    return False


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "upload.csv").name.strip() or "upload.csv"
    name = re.sub(r"[^A-Za-z0-9._\-一-鿿]+", "_", name)
    if name in {".", ".."}:
        return "upload.csv"
    return name[:120]


def sanitize_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "default").strip())
    return cleaned[:80] or "default"
