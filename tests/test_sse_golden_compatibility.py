from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agent_core.stream.event_stream import EventStream
from agent_core.stream.printer import Printer


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


CASES: dict[str, EmitCase] = {
    "start": _start,
    "text": _text,
    "tool_result": _tool_result,
    "result": _result,
    "error": _error,
    "done": _done,
}


@pytest.mark.parametrize("case_name", sorted(CASES))
async def test_sse_golden_case(case_name: str) -> None:
    stream = EventStream()
    printer = Printer(
        "golden-req",
        stream,
        conversation_id="golden-conv",
    )

    await CASES[case_name](printer)
    event = await stream._queue.get()

    expected = json.loads((FIXTURE_DIR / f"{case_name}.jsonl").read_text("utf-8"))
    assert event == expected
