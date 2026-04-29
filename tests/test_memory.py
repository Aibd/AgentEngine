from __future__ import annotations

from agent_core.memory.memory import Memory
from agent_core.memory.message import Message, Role


class TestMessage:
    def test_user_message(self):
        msg = Message.user("hello")
        assert msg.role == Role.USER
        assert msg.content == "hello"

    def test_system_message(self):
        msg = Message.system("sys")
        assert msg.role == Role.SYSTEM

    def test_assistant_message(self):
        msg = Message.assistant("reply")
        assert msg.role == Role.ASSISTANT
        assert msg.tool_calls is None

    def test_assistant_with_tool_calls(self):
        msg = Message.assistant("", tool_calls=[{"id": "c1"}])
        assert msg.tool_calls == [{"id": "c1"}]

    def test_tool_message(self):
        msg = Message.tool("result", tool_call_id="c1")
        assert msg.role == Role.TOOL
        assert msg.tool_call_id == "c1"

    def test_to_openai(self):
        msg = Message.user("hello")
        d = msg.to_openai()
        assert d == {"role": "user", "content": "hello"}

    def test_to_openai_with_tool_calls(self):
        tcs = [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}]
        msg = Message.assistant("", tool_calls=tcs)
        d = msg.to_openai()
        assert d["tool_calls"] == tcs

    def test_to_openai_with_reasoning_content(self):
        msg = Message.assistant("reply", reasoning_content="thinking")
        d = msg.to_openai()
        assert d["reasoning_content"] == "thinking"

    def test_to_openai_with_image(self):
        msg = Message.user("look", base64_image="abc123")
        d = msg.to_openai()
        assert d["content"] == [
            {"type": "text", "text": "look"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,abc123"},
            },
        ]

    def test_to_openai_with_image_data_url(self):
        msg = Message.user("look", base64_image="data:image/png;base64,abc123")
        d = msg.to_openai()
        assert d["content"][1]["image_url"]["url"] == "data:image/png;base64,abc123"


class TestMemory:
    def test_append_and_iterate(self):
        mem = Memory()
        mem.add_user_message("hello")
        mem.add_assistant_message("hi back")

        assert len(mem.messages) == 2
        assert mem.messages[0].content == "hello"
        assert mem.messages[1].content == "hi back"

    def test_to_openai(self):
        mem = Memory()
        mem.add_system_message("sys")
        mem.add_user_message("user")
        rendered = mem.to_openai()
        assert rendered[0] == {"role": "system", "content": "sys"}
        assert rendered[1] == {"role": "user", "content": "user"}

    def test_last_user_message(self):
        mem = Memory()
        mem.add_user_message("first")
        mem.add_assistant_message("reply")
        mem.add_user_message("second")
        assert mem.last_user_message() == "second"

    def test_last_user_message_empty(self):
        assert Memory().last_user_message() == ""

    def test_clear(self):
        mem = Memory()
        mem.add_user_message("hello")
        mem.clear()
        assert len(mem.messages) == 0

    def test_snapshot_returns_stable_copy(self):
        mem = Memory()
        mem.add_user_message("hello")

        snapshot = mem.snapshot()
        snapshot.clear()

        assert len(snapshot) == 0
        assert len(mem.messages) == 1
        assert mem.last_user_message() == "hello"

    def test_trim_disabled_by_default(self):
        mem = Memory()  # max_messages = 0 -> unbounded
        for i in range(50):
            mem.add_user_message(f"msg{i}")
        assert len(mem.messages) == 50

    def test_trim_preserves_system(self):
        mem = Memory(max_messages=3)
        mem.add_system_message("sys")
        for i in range(5):
            mem.add_user_message(f"msg{i}")

        assert len(mem.messages) <= 3
        roles = [m.role for m in mem.messages]
        assert Role.SYSTEM in roles
        # Newest messages should be kept
        assert mem.messages[-1].content == "msg4"

    def test_trim_max_one_keeps_only_system(self):
        mem = Memory(max_messages=1)
        mem.add_system_message("sys")
        mem.add_user_message("user")

        assert len(mem.messages) == 1
        assert mem.messages[0].role == Role.SYSTEM
        assert mem.messages[0].content == "sys"
