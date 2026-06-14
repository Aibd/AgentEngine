"""WebFetchTool — fetch a URL and return its text content.

Runs on the host (App process), not inside the sandbox container.
Strips HTML tags and returns readable plain text, size-capped.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)

_MAX_BYTES = 32_768   # 32 KB cap — enough for most pages, keeps context small
_TIMEOUT = 20.0

WEB_FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "The URL to fetch.",
        },
        "max_chars": {
            "type": "integer",
            "description": "Maximum characters to return (default 8000, max 32000).",
            "minimum": 1,
            "maximum": 32_000,
        },
    },
    "required": ["url"],
}


def _html_to_text(raw: str) -> str:
    """Very lightweight HTML → plain text: strip tags, collapse whitespace."""
    # Remove <script> and <style> blocks entirely
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    # Strip remaining tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities
    raw = html.unescape(raw)
    # Collapse whitespace
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch the content of a URL and return it as plain text. "
        "Use this to read a specific web page, article, or documentation. "
        "For broad searches use web_search instead."
    )
    schema = WEB_FETCH_SCHEMA
    timeout_seconds = _TIMEOUT

    async def run(self, **kwargs: Any) -> str:
        url = str(kwargs.get("url", "")).strip()
        if not url:
            return "Error: 'url' is required."
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"

        max_chars = int(kwargs.get("max_chars") or 8000)
        max_chars = max(1, min(max_chars, 32_000))

        try:
            import httpx
        except ImportError:
            return "Error: httpx is required. Install with: pip install httpx"

        headers = {"User-Agent": "AgentEngine/1.0 (web_fetch tool)"}

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                raw = resp.content[:_MAX_BYTES].decode("utf-8", errors="replace")
        except httpx.HTTPStatusError as exc:
            return f"Error: HTTP {exc.response.status_code} for {url}"
        except Exception as exc:
            return f"Error: fetch failed: {exc}"

        if "html" in content_type.lower():
            text = _html_to_text(raw)
        else:
            text = raw

        truncated = len(text) > max_chars
        text = text[:max_chars]

        header = f"# {url}"
        if truncated:
            header += f" (truncated to {max_chars} chars)"

        logger.info("web_fetch url=%s chars=%d", url, len(text))
        return f"{header}\n\n{text}"
