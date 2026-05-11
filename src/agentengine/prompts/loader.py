"""Load prompt templates from YAML files with caching.

Expected YAML shape::

    version: 1
    system: |
      You are a helpful assistant.
    templates:
      greeting: "Hello, {name}!"

Convenience methods read these conventional keys; `load()` returns the raw
parsed dict for callers that need everything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency at import time
    yaml = None  # type: ignore[assignment]


class PromptLoader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._cache: dict[str, dict[str, Any]] = {}

    def load(self, relative_path: str | Path, *, refresh: bool = False) -> dict[str, Any]:
        key = str(relative_path)
        if not refresh and key in self._cache:
            return self._cache[key]

        path = self.root / relative_path
        if not path.exists() and path.suffix == "":
            for ext in (".yaml", ".yml"):
                candidate = path.with_suffix(ext)
                if candidate.exists():
                    path = candidate
                    break
        if not path.exists():
            self._cache[key] = {}
            return {}

        text = path.read_text(encoding="utf-8")
        if yaml is None:
            data: dict[str, Any] = {"raw": text}
        else:
            parsed = yaml.safe_load(text) or {}
            if not isinstance(parsed, dict):
                raise ValueError(f"Prompt file must contain a mapping: {path}")
            data = parsed

        self._cache[key] = data
        return data

    # -- Convenience helpers -------------------------------------------

    def get_system_prompt(self, relative_path: str | Path) -> str:
        return _string_value(self.load(relative_path).get("system", ""))

    def get_template(self, relative_path: str | Path, template_name: str) -> str:
        templates = self.load(relative_path).get("templates", {})
        if not isinstance(templates, dict):
            return ""
        return _string_value(templates.get(template_name, ""))

    def clear_cache(self) -> None:
        self._cache.clear()


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""
