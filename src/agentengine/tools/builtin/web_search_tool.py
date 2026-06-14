"""WebSearchTool — search the web via Tavily API.

Runs on the host (App process), not inside the sandbox container.
Requires TAVILY_API_KEY environment variable.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agentengine.tools.base import Tool

logger = logging.getLogger(__name__)

_TAVILY_ENDPOINT = "https://api.tavily.com/search"

WEB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of results to return (1–10, default 5).",
            "minimum": 1,
            "maximum": 10,
        },
    },
    "required": ["query"],
}


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web and return a list of relevant results with titles, URLs, "
        "and content snippets. Use this when you need up-to-date information or "
        "facts not in your training data."
    )
    schema = WEB_SEARCH_SCHEMA
    timeout_seconds = 20.0

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("TAVILY_API_KEY", "")

    async def run(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: 'query' is required."
        if not self._api_key:
            return "Error: TAVILY_API_KEY is not set."

        max_results = int(kwargs.get("max_results") or 5)
        max_results = max(1, min(max_results, 10))

        try:
            import httpx
        except ImportError:
            return "Error: httpx is required. Install with: pip install httpx"

        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(_TAVILY_ENDPOINT, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            return f"Error: Tavily API returned {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:
            return f"Error: web search failed: {exc}"

        results = data.get("results", [])
        if not results:
            return f"No results found for: {query}"

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.get('title', '(no title)')}")
            lines.append(f"    URL: {r.get('url', '')}")
            content = r.get("content", "").strip()
            if content:
                lines.append(f"    {content[:300]}")
            lines.append("")

        logger.info("web_search query=%r results=%d", query, len(results))
        return "\n".join(lines)
