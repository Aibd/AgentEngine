"""Run an agent and stream its events through the Claude Code-style renderer.

Usage::

    uv run python scripts/chat_pretty.py general_chat "你好,介绍下这个项目"
    uv run python scripts/chat_pretty.py deep_research "调研 X 项目结构" --show-reasoning expanded
    uv run python scripts/chat_pretty.py general_chat "..." --show-reasoning hidden

Requires ``LLM_API_KEY`` and ``LLM_MODEL`` in the environment (or `.env`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentengine.llm.factory import create_llm_from_env  # noqa: E402

from rich.console import Console  # noqa: E402

from renderers.rich_renderer import ReasoningMode, RichRenderer  # noqa: E402
from examples.reference_app.services import AgentOrchestrationService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an agent with the pretty terminal renderer.")
    parser.add_argument("agent_name", help="Agent name, e.g. general_chat or deep_research")
    parser.add_argument("query", nargs="+", help="User query text")
    parser.add_argument("--request-id", default="cli-pretty", help="Request id for this run")
    parser.add_argument("--conversation-id", default="cli-conv", help="Conversation id for this run")
    parser.add_argument(
        "--show-reasoning",
        choices=[m.value for m in ReasoningMode],
        default=ReasoningMode.COLLAPSED.value,
        help="How to render the model's chain-of-thought (default: collapsed, Kimi-style)",
    )
    return parser.parse_args()


async def _drain_stream(stream, renderer: RichRenderer) -> None:
    async for event in stream:
        if event is None:
            break
        renderer.handle(event)


async def run() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    args = parse_args()
    query = " ".join(args.query)
    console = Console()

    # Bail early on missing credentials (clearer than a generic LLM error).
    if not os.getenv("LLM_API_KEY") or not os.getenv("LLM_MODEL"):
        console.print("[bold red]✗ Missing LLM_API_KEY or LLM_MODEL[/bold red]")
        console.print("Set them in your shell or in a .env file. See README.md.")
        return 2

    try:
        llm = create_llm_from_env(required=True)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]✗ Failed to create LLM client:[/bold red] {exc}")
        return 2

    service = AgentOrchestrationService()
    context, stream = service.create_streaming_context(
        request_id=args.request_id,
        query=query,
        conversation_id=args.conversation_id,
    )
    context.llm = llm

    renderer = RichRenderer(
        agent_name=args.agent_name,
        console=console,
        reasoning=ReasoningMode(args.show_reasoning),
    )

    async def _runner() -> None:
        try:
            await service.run(
                agent_name=args.agent_name,
                query=query,
                context=context,
            )
        finally:
            # Always close the stream so the renderer's drain loop exits.
            await stream.close()

    runner_task = asyncio.create_task(_runner())
    drain_task = asyncio.create_task(_drain_stream(stream, renderer))

    try:
        await asyncio.gather(runner_task, drain_task)
    except KeyboardInterrupt:
        runner_task.cancel()
        drain_task.cancel()
        console.print("[bold yellow]⚠ Cancelled by user[/bold yellow]")
        return 130
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]✗ Run failed:[/bold red] {exc}")
        renderer.finish()
        await llm.close()
        return 1

    renderer.finish()
    await llm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
