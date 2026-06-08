"""MCP (Model Context Protocol) connector configuration store.

Stores connector configs as JSON on disk so the frontend can manage
connections without restarting the backend.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

DEFAULT_STORE_NAME = "mcp_connectors.json"


@dataclass(slots=True)
class McpConnector:
    id: str
    name: str
    transport: str  # "stdio" | "sse"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    enabled: bool = True

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class McpConnectorStore:
    """JSON-file-backed CRUD store for MCP connector configurations."""

    def __init__(self, file_path: Path | str) -> None:
        self._path = Path(file_path)

    # -- read / write -----------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            logger.exception("mcp_connectors_read_error")
            return []

    def _save(self, connectors: list[McpConnector]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [c.snapshot() for c in connectors]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- public API -------------------------------------------------------

    def list_all(self) -> list[McpConnector]:
        rows = self._load()
        return [_row_to_connector(r) for r in rows]

    def get(self, connector_id: str) -> McpConnector | None:
        for c in self.list_all():
            if c.id == connector_id:
                return c
        return None

    def add(self, connector: McpConnector) -> McpConnector:
        if not connector.id:
            connector.id = uuid.uuid4().hex[:8]
        all_connectors = self.list_all()
        all_connectors.append(connector)
        self._save(all_connectors)
        logger.info("mcp_connector_added id=%s name=%s transport=%s", connector.id, connector.name, connector.transport)
        return connector

    def update(self, connector_id: str, patch: dict[str, Any]) -> McpConnector | None:
        all_connectors = self.list_all()
        for i, c in enumerate(all_connectors):
            if c.id == connector_id:
                for key in ("name", "transport", "command", "args", "env", "url", "enabled"):
                    if key in patch:
                        setattr(c, key, patch[key])
                self._save(all_connectors)
                logger.info("mcp_connector_updated id=%s", connector_id)
                return c
        return None

    def delete(self, connector_id: str) -> bool:
        all_connectors = self.list_all()
        filtered = [c for c in all_connectors if c.id != connector_id]
        if len(filtered) == len(all_connectors):
            return False
        self._save(filtered)
        logger.info("mcp_connector_deleted id=%s", connector_id)
        return True

    def toggle(self, connector_id: str) -> McpConnector | None:
        c = self.get(connector_id)
        if c is None:
            return None
        return self.update(connector_id, {"enabled": not c.enabled})


def _row_to_connector(row: dict[str, Any]) -> McpConnector:
    return McpConnector(
        id=str(row.get("id", "")),
        name=str(row.get("name", "")),
        transport=str(row.get("transport", "stdio")),
        command=str(row.get("command", "")),
        args=[str(a) for a in row.get("args", []) or []],
        env={str(k): str(v) for k, v in (row.get("env") or {}).items()},
        url=str(row.get("url", "")),
        enabled=bool(row.get("enabled", True)),
    )
