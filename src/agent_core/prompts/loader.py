from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


class PromptLoader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(self, relative_path: str | Path) -> dict[str, Any]:
        path = self.root / relative_path
        text = path.read_text(encoding="utf-8")
        if yaml is None:
            return {"raw": text}
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Prompt file must contain a mapping: {path}")
        return data
