from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.services.reporting.models import ParsedFile
from examples.services.reporting.parser import parse_attachment_metadata, parse_financial_file


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PARSABLE_EXTENSIONS = {".csv", ".xlsx"}
ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_EXTENSIONS = PARSABLE_EXTENSIONS | ATTACHMENT_EXTENSIONS


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
    def __init__(self, root: Path) -> None:
        self._root = root
        self._records: dict[str, ReportFileRecord] = {}

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
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"unsupported file extension: {extension or '(none)'}")
        if not data:
            raise ValueError("uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("uploaded file exceeds 50MB limit")

        file_id = f"file_{uuid.uuid4().hex[:12]}"
        directory = (self._root / sanitize_segment(tenant_id) / file_id).resolve()
        root = self._root.resolve()
        if not directory.is_relative_to(root):
            raise ValueError("invalid upload path")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / safe_name
        await asyncio.to_thread(path.write_bytes, data)
        if extension in PARSABLE_EXTENSIONS:
            parsed = await asyncio.to_thread(
                parse_financial_file,
                file_id=file_id,
                filename=safe_name,
                path=path,
                size_bytes=len(data),
            )
        else:
            parsed = parse_attachment_metadata(
                file_id=file_id,
                filename=safe_name,
                extension=extension,
                size_bytes=len(data),
                path=path,
            )
        record = ReportFileRecord(
            id=file_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            filename=safe_name,
            path=path,
            size_bytes=len(data),
            parsed=parsed,
        )
        self._records[file_id] = record
        return record

    def get(self, file_id: str) -> ReportFileRecord | None:
        return self._records.get(file_id)

    def get_many(self, file_ids: list[str]) -> list[ReportFileRecord]:
        return [record for file_id in file_ids if (record := self.get(file_id)) is not None]

    def list_by_conversation(self, conversation_id: str) -> list[ReportFileRecord]:
        return [
            record
            for record in self._records.values()
            if record.conversation_id == conversation_id
        ]


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "upload.csv").name.strip() or "upload.csv"
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name)
    if name in {".", ".."}:
        return "upload.csv"
    return name[:120]


def sanitize_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "default").strip())
    return cleaned[:80] or "default"
