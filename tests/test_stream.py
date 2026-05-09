from __future__ import annotations

from agentengine.errors import LLMTimeoutError
from agentengine.stream.events import EventType
from agentengine.stream.printer import Printer
from agentengine.stream.sse_queue import SseEventQueue


class TestSseEventQueue:
    async def test_put_and_iterate(self):
        stream = SseEventQueue()
        await stream.put({"type": "text", "content": "hello"})
        await stream.close()

        events = []
        async for event in stream:
            events.append(event)
        assert len(events) == 1
        assert events[0]["content"] == "hello"

    async def test_close_stops_iteration(self):
        stream = SseEventQueue()
        await stream.close()
        events = [e async for e in stream]
        assert events == []

    async def test_put_after_close_is_noop(self):
        stream = SseEventQueue()
        await stream.close()
        await stream.put({"type": "text"})
        events = [e async for e in stream]
        assert events == []

    async def test_order_preserved(self):
        stream = SseEventQueue()
        for i in range(5):
            await stream.put({"idx": i})
        await stream.close()
        events = [e async for e in stream]
        assert [e["idx"] for e in events] == [0, 1, 2, 3, 4]


class TestPrinter:
    async def test_text_event(self):
        stream = SseEventQueue()
        printer = Printer("req-1", stream)
        await printer.text("hello")
        event = await stream._queue.get()
        assert event["event"] == "text"
        assert event["data"]["delta"] == "hello"
        assert event["data"]["request_id"] == "req-1"

    async def test_done_marks_finished(self):
        stream = SseEventQueue()
        printer = Printer("req-2", stream)
        await printer.done()
        event = await stream._queue.get()
        assert event["event"] == "done"
        assert event["data"]["reason"] == "completed"

    async def test_error_marks_finished_and_sets_error_msg(self):
        stream = SseEventQueue()
        printer = Printer("req-3", stream)
        await printer.error("boom")
        event = await stream._queue.get()
        assert event["event"] == "error"
        assert event["data"]["message"] == "boom"

    async def test_error_serializes_structured_exception(self):
        stream = SseEventQueue()
        printer = Printer("req-err", stream)
        await printer.error(LLMTimeoutError("provider timed out"))
        event = await stream._queue.get()

        assert event["event"] == "error"
        assert event["data"]["message"] == "provider timed out"
        assert event["data"]["code"] == "llm_timeout"
        assert event["data"]["category"] == "llm"
        assert event["data"]["retryable"] is True

    async def test_tool_result_populates_result_map(self):
        stream = SseEventQueue()
        printer = Printer("req-4", stream)
        await printer.tool_result("echo", "hi")
        event = await stream._queue.get()
        assert event["event"] == "tool_result"
        assert event["data"] == {
            "tool": "echo",
            "ok": True,
            "result": "hi",
            "elapsed_ms": 0,
            "tool_call_id": "",
            "request_id": "req-4",
            "conversation_id": "",
        }

    async def test_send_uses_event_data_shape(self):
        stream = SseEventQueue()
        printer = Printer("req-5", stream)
        await printer.send(EventType.TEXT, {"delta": "x"})
        event = await stream._queue.get()
        assert event == {
            "event": "text",
            "data": {
                "delta": "x",
                "request_id": "req-5",
                "conversation_id": "",
            },
        }

    async def test_conversation_id_propagates(self):
        stream = SseEventQueue()
        printer = Printer("r1", stream, conversation_id="conv-42")
        await printer.text("msg")
        event = await stream._queue.get()
        assert event["data"]["conversation_id"] == "conv-42"

    async def test_v1_envelope_keys_removed(self):
        stream = SseEventQueue()
        printer = Printer("r1", stream, conversation_id="conv-1")
        await printer.text("hi")
        event = await stream._queue.get()
        assert set(event) == {"event", "data"}
        assert "responseType" not in event
        assert "responseAll" not in event
        assert "useTimes" not in event
