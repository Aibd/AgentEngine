from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

from agent_core.runtime.events import RuntimeEvent

logger = logging.getLogger(__name__)

_MAX_SIZE_BYTES = 10 * 1024 * 1024


class RunEventLog:
    """Append-only jsonl event log for a single run."""

    def __init__(self, run_id: str, base_dir: str | Path | None = None) -> None:
        self.run_id = run_id
        base_value = base_dir if base_dir is not None else os.getenv("AGENT_CORE_LOG_DIR", "logs")
        base = Path(base_value)
        self.dir = base / "runs" / date.today().isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{run_id}.jsonl"
        self._truncated = False

    def append(self, event: RuntimeEvent) -> None:
        if self._truncated:
            return
        if self.path.exists() and self.path.stat().st_size > _MAX_SIZE_BYTES:
            self._truncated = True
            truncated_path = self.path.with_suffix(".jsonl.truncated")
            self.path.rename(truncated_path)
            logger.warning(
                "run event log truncated: run_id=%s path=%s",
                self.run_id,
                truncated_path,
            )
            return

        record = event.to_dict()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=_json_default))
            handle.write("\n")

    def read_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _json_default(value: Any) -> str:
    return str(value)
