#!/usr/bin/env python3
"""Web search via Tavily API.

Usage: search.py <query> [max_results]
"""
import os
import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Error: query argument is required", file=sys.stderr)
        sys.exit(1)

    query = args[0]
    max_results = int(args[1]) if len(args) > 1 else 5
    max_results = max(1, min(max_results, 10))

    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        print("Error: TAVILY_API_KEY environment variable is not set", file=sys.stderr)
        sys.exit(1)

    try:
        import httpx
    except ImportError:
        print("Error: httpx is not installed", file=sys.stderr)
        sys.exit(1)

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"Error: Tavily API {exc.response.status_code}: {exc.response.text[:200]}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    results = data.get("results", [])
    if not results:
        print(f"No results found for: {query}")
        return

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', '(no title)')}")
        lines.append(f"    URL: {r.get('url', '')}")
        content = r.get("content", "").strip()
        if content:
            lines.append(f"    {content[:300]}")
        lines.append("")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
