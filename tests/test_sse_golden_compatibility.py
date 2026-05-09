from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agentkit.stream.printer import Printer
from agentkit.stream.sse_queue import SseEventQueue


EmitCase = Callable[[Printer], Awaitable[None]]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sse_golden"


async def _start(printer: Printer) -> None:
    await printer.start("hello")


async def _text(printer: Printer) -> None:
    await printer.text("chunk")


async def _tool_result(printer: Printer) -> None:
    await printer.tool_result("echo", "hi")


async def _result(printer: Printer) -> None:
    await printer.result({"result": "done"})


async def _error(printer: Printer) -> None:
    await printer.error("boom")


async def _done(printer: Printer) -> None:
    await printer.done()


async def _thinking(printer: Printer) -> None:
    await printer.thinking("reasoning chunk")


async def _tool_call_start(printer: Printer) -> None:
    await printer.tool_call_start("echo", {"arg": "val"})


async def _step(printer: Printer) -> None:
    await printer.step(1)


async def _step_end(printer: Printer) -> None:
    await printer.step_end(1, has_tool_calls=True, elapsed_seconds=1.5)


async def _usage(printer: Printer) -> None:
    await printer.usage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        total_seconds=2.5,
    )


CASES: dict[str, EmitCase] = {
    "start": _start,
    "text": _text,
    "tool_result": _tool_result,
    "result": _result,
    "error": _error,
    "done": _done,
    "thinking": _thinking,
    "tool_call_start": _tool_call_start,
    "step": _step,
    "step_end": _step_end,
    "usage": _usage,
}


@pytest.mark.parametrize("case_name", sorted(CASES))
async def test_sse_golden_case(case_name: str) -> None:
    stream = SseEventQueue()
    printer = Printer(
        "golden-req",
        stream,
        conversation_id="golden-conv",
    )

    await CASES[case_name](printer)
    event = await stream._queue.get()

    expected = json.loads((FIXTURE_DIR / f"{case_name}.jsonl").read_text("utf-8"))
    assert event == expected
