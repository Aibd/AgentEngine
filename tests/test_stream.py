from __future__ import annotations

from agent_core.errors import LLMTimeoutError
from agent_core.stream.event_stream import EventStream
from agent_core.stream.events import EventType
from agent_core.stream.printer import Printer


class TestEventStream:
    async def test_put_and_iterate(self):
        stream = EventStream()
        await stream.put({"type": "text", "content": "hello"})
        await stream.close()

        events = []
        async for event in stream:
            events.append(event)
        assert len(events) == 1
        assert events[0]["content"] == "hello"

    async def test_close_stops_iteration(self):
        stream = EventStream()
        await stream.close()
        events = [e async for e in stream]
        assert events == []

    async def test_put_after_close_is_noop(self):
        stream = EventStream()
        await stream.close()
        await stream.put({"type": "text"})
        events = [e async for e in stream]
        assert events == []

    async def test_order_preserved(self):
        stream = EventStream()
        for i in range(5):
            await stream.put({"idx": i})
        await stream.close()
        events = [e async for e in stream]
        assert [e["idx"] for e in events] == [0, 1, 2, 3, 4]


class TestPrinter:
    async def test_text_event(self):
        stream = EventStream()
        printer = Printer("req-1", stream)
        await printer.text("hello")
        event = await stream._queue.get()
        assert event["responseType"] == "text"
        assert event["response"] == "hello"
        assert event["finished"] is False
        assert event["reqId"] == "req-1"

    async def test_done_marks_finished(self):
        stream = EventStream()
        printer = Printer("req-2", stream)
        await printer.done()
        event = await stream._queue.get()
        assert event["responseType"] == "done"
        assert event["finished"] is True

    async def test_error_marks_finished_and_sets_error_msg(self):
        stream = EventStream()
        printer = Printer("req-3", stream)
        await printer.error("boom")
        event = await stream._queue.get()
        assert event["responseType"] == "error"
        assert event["finished"] is True
        assert event["errorMsg"] == "boom"

    async def test_error_serializes_structured_exception(self):
        stream = EventStream()
        printer = Printer("req-err", stream)
        await printer.error(LLMTimeoutError("provider timed out"))
        event = await stream._queue.get()

        assert event["responseType"] == "error"
        assert event["errorMsg"] == "provider timed out"
        assert event["response"]["code"] == "llm_timeout"
        assert event["response"]["category"] == "llm"
        assert event["response"]["retryable"] is True

    async def test_tool_result_populates_result_map(self):
        stream = EventStream()
        printer = Printer("req-4", stream)
        await printer.tool_result("echo", "hi")
        event = await stream._queue.get()
        assert event["responseType"] == "tool_result"
        assert event["resultMap"] == {
            "tool": "echo",
            "toolResult": "hi",
            "ok": True,
            "elapsed_seconds": 0.0,
            "tool_call_id": "",
        }
        # response is still the simple string for legacy front ends
        assert event["response"] == "hi"

    async def test_send_with_explicit_finished(self):
        stream = EventStream()
        printer = Printer("req-5", stream)
        await printer.send(EventType.TEXT, "x", finished=True)
        event = await stream._queue.get()
        assert event["finished"] is True

    async def test_conversation_id_propagates(self):
        stream = EventStream()
        printer = Printer("r1", stream, conversation_id="conv-42")
        await printer.text("msg")
        event = await stream._queue.get()
        assert event["conversation_id"] == "conv-42"

    async def test_full_sse_envelope_keys(self):
        """Verify the line-side SSE contract has not regressed."""
        stream = EventStream()
        printer = Printer("r1", stream, conversation_id="conv-1")
        await printer.text("hi")
        event = await stream._queue.get()
        for key in (
            "responseType",
            "response",
            "responseAll",
            "useTimes",
            "reqId",
            "errorMsg",
            "resultMap",
            "conversation_id",
            "finished",
        ):
            assert key in event, f"missing SSE key: {key}"
