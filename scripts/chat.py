"""Small CLI for running an agent with the configured LLM provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_core.base.context import AgentContext  # noqa: E402
from agent_core.llm.factory import create_llm_from_env  # noqa: E402
from agent_core.stream.printer import Printer  # noqa: E402
from agent_core.stream.sse_queue import SseEventQueue  # noqa: E402
from services import AgentOrchestrationService  # noqa: E402


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an agent from the command line.")
    parser.add_argument("agent_name", help="Agent name, e.g. general_chat or deep_research")
    parser.add_argument("query", nargs="+", help="User query text")
    parser.add_argument(
        "--config",
        default="config/agents.yaml",
        help="Path to agents config YAML",
    )
    parser.add_argument("--request-id", default="cli", help="Request id for this run")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override the agent max_steps setting for this run",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Print stream events and memory messages after the run",
    )
    return parser.parse_args()


async def run() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    query = " ".join(args.query)
    llm = create_llm_from_env(required=True)
    assert llm is not None

    event_stream = SseEventQueue() if args.trace else None
    printer = (
        Printer(
            request_id=args.request_id,
            event_stream=event_stream,
            conversation_id=args.request_id,
        )
        if event_stream is not None
        else None
    )
    context = AgentContext(
        request_id=args.request_id,
        query=query,
        llm=llm,
        printer=printer,
        conversation_id=args.request_id if args.trace else "",
    )

    try:
        agent_kwargs = {"max_steps": args.max_steps} if args.max_steps is not None else None
        result = await AgentOrchestrationService(config_path=args.config).run(
            agent_name=args.agent_name,
            query=query,
            context=context,
            agent_kwargs=agent_kwargs,
        )
    finally:
        close: Any = getattr(llm, "close", None)
        if close is not None:
            await close()

    print(result)
    if args.trace:
        print_trace(context, event_stream)
    return 0


def print_trace(context: AgentContext, event_stream: SseEventQueue | None) -> None:
    print("\n--- events ---")
    for event in drain_events(event_stream):
        print(json.dumps(event, ensure_ascii=False, indent=2))

    print("\n--- memory ---")
    for index, message in enumerate(context.extras.get("agent_memory", []), start=1):
        print(f"[{index}] {json.dumps(message, ensure_ascii=False, indent=2)}")


def drain_events(event_stream: SseEventQueue | None) -> list[dict[str, Any]]:
    if event_stream is None:
        return []
    events: list[dict[str, Any]] = []
    while not event_stream._queue.empty():
        event = event_stream._queue.get_nowait()
        if event is not None:
            events.append(event)
    return events


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
