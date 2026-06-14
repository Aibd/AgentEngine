#!/usr/bin/env python3
"""Fetch a URL and return its content as plain text.

Usage: fetch.py <url> [max_chars]
"""
import html as html_mod
import re
import sys


_MAX_BYTES = 32_768


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_mod.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Error: url argument is required", file=sys.stderr)
        sys.exit(1)

    url = args[0]
    if not url.startswith(("http://", "https://")):
        print("Error: URL must start with http:// or https://", file=sys.stderr)
        sys.exit(1)

    max_chars = int(args[1]) if len(args) > 1 else 8000
    max_chars = max(1, min(max_chars, 32_000))

    try:
        import httpx
    except ImportError:
        print("Error: httpx is not installed", file=sys.stderr)
        sys.exit(1)

    headers = {"User-Agent": "AgentEngine/1.0 (web-fetch skill)"}

    try:
        resp = httpx.get(url, timeout=20.0, follow_redirects=True, headers=headers)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        raw = resp.content[:_MAX_BYTES].decode("utf-8", errors="replace")
    except httpx.HTTPStatusError as exc:
        print(f"Error: HTTP {exc.response.status_code} for {url}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    text = _html_to_text(raw) if "html" in content_type.lower() else raw
    truncated = len(text) > max_chars
    text = text[:max_chars]

    header = f"# {url}"
    if truncated:
        header += f" (truncated to {max_chars} chars)"

    print(f"{header}\n\n{text}")


if __name__ == "__main__":
    main()
