"""Tests for the SQLite persistence backend.

Validates:
  - PersistencePort protocol compliance (runtime_checkable)
  - Round-trip for messages (save → load preserves OpenAI dict shape)
  - Multi-conversation isolation (no cross-conversation leakage)
  - Run records and artifacts persist
  - Memory.load_from_db / save_to_db integrate cleanly
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine.memory.memory import Memory
from agentengine.memory.message import Message, Role
from agentengine.persistence import PersistencePort, SqlitePersistence


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture
def store(db_path: str) -> SqlitePersistence:
    return SqlitePersistence(db_path)


class TestProtocolCompliance:
    async def test_satisfies_persistence_port(self, store: SqlitePersistence) -> None:
        # Trigger init so the runtime_checkable Protocol sees a fully-formed
        # instance (init is lazy).
        await store.load_messages("nonexistent")
        assert isinstance(store, PersistencePort)


class TestMessages:
    async def test_round_trip_preserves_openai_dicts(self, store: SqlitePersistence) -> None:
        original = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "reasoning_content": "think first"},
        ]
        await store.save_messages("conv-1", original)
        loaded = await store.load_messages("conv-1")
        assert loaded == original

    async def test_load_unknown_conversation_returns_empty(self, store: SqlitePersistence) -> None:
        assert await store.load_messages("does-not-exist") == []

    async def test_load_with_blank_conversation_id_returns_empty(self, store: SqlitePersistence) -> None:
        assert await store.load_messages("") == []

    async def test_save_with_blank_conversation_id_is_noop(self, store: SqlitePersistence) -> None:
        await store.save_messages("", [{"role": "user", "content": "x"}])
        # Nothing was persisted, so load against any id still yields []
        assert await store.load_messages("anything") == []

    async def test_save_replaces_previous_messages(self, store: SqlitePersistence) -> None:
        await store.save_messages("conv-1", [{"role": "user", "content": "v1"}])
        await store.save_messages(
            "conv-1",
            [{"role": "user", "content": "v2"}, {"role": "assistant", "content": "ok"}],
        )
        loaded = await store.load_messages("conv-1")
        assert loaded == [
            {"role": "user", "content": "v2"},
            {"role": "assistant", "content": "ok"},
        ]

    async def test_conversations_are_isolated(self, store: SqlitePersistence) -> None:
        await store.save_messages("alice", [{"role": "user", "content": "alice's msg"}])
        await store.save_messages("bob", [{"role": "user", "content": "bob's msg"}])

        assert await store.load_messages("alice") == [
            {"role": "user", "content": "alice's msg"}
        ]
        assert await store.load_messages("bob") == [
            {"role": "user", "content": "bob's msg"}
        ]

    async def test_preserves_tool_calls(self, store: SqlitePersistence) -> None:
        original = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "content": "echoed: hi",
                "tool_call_id": "call_1",
            },
        ]
        await store.save_messages("conv-tools", original)
        loaded = await store.load_messages("conv-tools")
        assert loaded == original

    async def test_message_order_preserved(self, store: SqlitePersistence) -> None:
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        await store.save_messages("conv-order", msgs)
        loaded = await store.load_messages("conv-order")
        assert [m["content"] for m in loaded] == [f"msg {i}" for i in range(20)]

    async def test_round_trip_preserves_metadata(self, store: SqlitePersistence) -> None:
        original = [
            {"role": "tool", "content": "activated", "tool_call_id": "c1", "metadata": {"skill_activation": True}}
        ]
        await store.save_messages("conv-meta", original)
        loaded = await store.load_messages("conv-meta")
        assert loaded == original

    async def test_memory_save_and_load_preserves_metadata(self, store: SqlitePersistence) -> None:
        memory = Memory()
        memory.add_system_message("sys")
        memory.add_tool_message("activated", tool_call_id="c1")
        memory.messages[-1].metadata = {"skill_activation": True}

        await memory.save_to_db(store, "conv-mem")
        loaded = Memory()
        await loaded.load_from_db(store, "conv-mem")

        tool_msg = loaded.messages[-1]
        assert tool_msg.role == Role.TOOL
        assert tool_msg.metadata == {"skill_activation": True}


class TestRuns:
    async def test_save_and_list(self, store: SqlitePersistence) -> None:
        await store.save_run(
            run_id="run-1",
            conversation_id="conv-1",
            agent_name="general_chat",
            input_msg="q1",
            reply_msg="a1",
            metadata={"steps": 1},
        )
        await store.save_run(
            run_id="run-2",
            conversation_id="conv-1",
            agent_name="general_chat",
            input_msg="q2",
            reply_msg="a2",
        )

        runs = await store.list_runs("conv-1")
        # Newest first
        assert [r["run_id"] for r in runs] == ["run-2", "run-1"]
        assert runs[1]["metadata"] == {"steps": 1}

    async def test_list_isolates_per_conversation(self, store: SqlitePersistence) -> None:
        await store.save_run(
            run_id="r-a", conversation_id="alice",
            agent_name="x", input_msg="q", reply_msg="a",
        )
        await store.save_run(
            run_id="r-b", conversation_id="bob",
            agent_name="x", input_msg="q", reply_msg="a",
        )
        assert [r["run_id"] for r in await store.list_runs("alice")] == ["r-a"]
        assert [r["run_id"] for r in await store.list_runs("bob")] == ["r-b"]

    async def test_save_run_upserts_on_conflict(self, store: SqlitePersistence) -> None:
        await store.save_run(
            run_id="run-x", conversation_id="c",
            agent_name="a", input_msg="q1", reply_msg="r1",
        )
        await store.save_run(
            run_id="run-x", conversation_id="c",
            agent_name="a", input_msg="q2", reply_msg="r2",
        )
        runs = await store.list_runs("c")
        assert len(runs) == 1
        assert runs[0]["reply_msg"] == "r2"


class TestArtifacts:
    async def test_save_artifact(self, store: SqlitePersistence) -> None:
        # No public reader yet — this just verifies the call path doesn't blow up
        # and that re-saving with the same run_id appends, not collides.
        await store.save_artifact("run-1", "search_results", {"query": "q", "hits": 3})
        await store.save_artifact("run-1", "search_results", {"query": "q2", "hits": 5})
        # Smoke: inspect through raw connection
        with store._connect() as conn:
            cursor = conn.execute(
                "SELECT artifact_type, data FROM artifacts WHERE run_id = ? ORDER BY id",
                ("run-1",),
            )
            rows = cursor.fetchall()
        assert len(rows) == 2
        assert rows[0]["artifact_type"] == "search_results"


class TestMemoryIntegration:
    async def test_memory_save_and_reload(self, store: SqlitePersistence) -> None:
        memory = Memory()
        memory.add_system_message("you are helpful")
        memory.add_user_message("first question")
        memory.add_assistant_message("first answer")
        await memory.save_to_db(store, "conv-1")

        # Simulate a fresh request: build a new Memory, load history.
        revived = Memory()
        revived.add_system_message("you are helpful")  # spec-injected on each run
        await revived.load_from_db(store, "conv-1")

        roles = [m.role for m in revived.messages]
        contents = [m.content for m in revived.messages]
        # System stays at the top, prior turns appear after, no duplicates
        assert roles[0] == Role.SYSTEM
        assert contents[0] == "you are helpful"
        assert sum(role == Role.SYSTEM for role in roles) == 1
        assert "first question" in contents
        assert "first answer" in contents

    async def test_memory_round_trip_preserves_tool_calls(
        self,
        store: SqlitePersistence,
    ) -> None:
        memory = Memory()
        memory.add_assistant_message(
            "",
            tool_calls=[{
                "id": "c1",
                "type": "function",
                "function": {"name": "echo", "arguments": "{}"},
            }],
        )
        memory.append(Message.tool("echoed", tool_call_id="c1"))
        await memory.save_to_db(store, "conv-tool")

        revived = Memory()
        await revived.load_from_db(store, "conv-tool")

        assistant = next(m for m in revived.messages if m.role == Role.ASSISTANT)
        assert assistant.tool_calls is not None
        assert assistant.tool_calls[0]["function"]["name"] == "echo"

        tool_msg = next(m for m in revived.messages if m.role == Role.TOOL)
        assert tool_msg.tool_call_id == "c1"
        assert tool_msg.content == "echoed"


class TestSchemaIdempotence:
    async def test_reopen_existing_db_works(self, db_path: str) -> None:
        store_a = SqlitePersistence(db_path)
        await store_a.save_messages("c", [{"role": "user", "content": "v1"}])

        store_b = SqlitePersistence(db_path)
        loaded = await store_b.load_messages("c")
        assert loaded == [{"role": "user", "content": "v1"}]


class TestServiceIntegration:
    """End-to-end: two successive service.run() calls share conversation history."""

    async def test_second_call_sees_first_conversation_history(self, db_path: str) -> None:
        from agentengine.llm.interfaces import LLMResponse
        from mock_llm import MockLLMClient
        from app.backend.services.agent_orchestration_service import AgentOrchestrationService

        store = SqlitePersistence(db_path)

        # First call: greet, store "hello" + "hi there" in DB.
        first_llm = MockLLMClient([LLMResponse(content="hi there", finish_reason="stop")])
        service_one = AgentOrchestrationService(
            llm_factory=lambda: first_llm,
            persistence=store,
        )
        result_one = await service_one.run(
            agent_name="general_chat",
            query="hello",
            context=None,
        )
        assert result_one == "hi there"

        # Verify the DB now has the first turn under the implicit conversation id.
        # `service.run` defaults to request_id="local" and conversation_id="";
        # without conversation_id, no save happens. Force a real conversation id
        # by passing a context.
        from agentengine.base.context import AgentContext
        ctx = AgentContext(
            request_id="r1",
            query="first",
            conversation_id="conv-x",
        )
        await service_one.run(
            agent_name="general_chat",
            query="what's my name?",
            context=ctx,
        )

        stored = await store.load_messages("conv-x")
        assert any(m["role"] == "user" and "what's my name" in m["content"] for m in stored)

        # Second call with the same conversation_id should rehydrate history.
        # We re-spawn the LLM/service to simulate a fresh process.
        second_llm = MockLLMClient([LLMResponse(content="recalled", finish_reason="stop")])
        service_two = AgentOrchestrationService(
            llm_factory=lambda: second_llm,
            persistence=store,
        )
        ctx2 = AgentContext(
            request_id="r2",
            query="follow up",
            conversation_id="conv-x",
        )
        await service_two.run(
            agent_name="general_chat",
            query="follow up",
            context=ctx2,
        )

        # The second LLM call should have seen prior assistant messages in its prompt.
        sent_messages = second_llm.calls[0]["messages"]
        roles = [m["role"] for m in sent_messages]
        # Expect at minimum: prior user, prior assistant, current user
        user_messages = [m for m in sent_messages if m["role"] == "user"]
        assistant_messages = [m for m in sent_messages if m["role"] == "assistant"]
        assert len(user_messages) >= 2, f"expected history replay, got roles={roles}"
        assert len(assistant_messages) >= 1, f"expected prior assistant turns, got roles={roles}"

    async def test_service_without_persistence_still_works(self) -> None:
        from agentengine.llm.interfaces import LLMResponse
        from mock_llm import MockLLMClient
        from app.backend.services.agent_orchestration_service import AgentOrchestrationService

        llm = MockLLMClient([LLMResponse(content="ok", finish_reason="stop")])
        service = AgentOrchestrationService(llm_factory=lambda: llm)
        result = await service.run(agent_name="general_chat", query="hi")
        assert result == "ok"
