"""Concurrency / conversation isolation tests.

The framework's persistence layer reads → mutates → writes the conversation
on every run. Two concurrent runs with the same conversation_id therefore
race: whichever writes last wins, and the loser's turn is silently dropped.

These tests:

  1. Verify InMemoryConversationLockManager itself behaves like a Lock.
  2. Prove the unlocked path actually loses turns (control case).
  3. Prove the Service-integrated lock prevents that loss.
  4. Verify that distinct conversation_ids don't block each other.
  5. Verify that empty conversation_ids are not serialized.
  6. Verify lock GC: locks are released when no waiter remains.

Without (2), (3) would prove only that the test framework runs in serial
under the hood and not that the lock matters.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from pathlib import Path

import pytest

from agent_core.base.context import AgentContext
from agent_core.concurrency import (
    ConversationLockManager,
    InMemoryConversationLockManager,
)
from agent_core.llm.client import LLMChunk, LLMResponse
from agent_core.persistence import SqlitePersistence
from mock_llm import MockLLMClient
from services.agent_orchestration_service import AgentOrchestrationService


# ---------------------------------------------------------------------------
# 1. Lock manager unit tests
# ---------------------------------------------------------------------------

class TestInMemoryConversationLockManager:
    async def test_serializes_same_id(self) -> None:
        manager = InMemoryConversationLockManager()
        order: list[str] = []

        async def task(label: str, hold_for: float) -> None:
            async with manager.acquire("conv-1"):
                order.append(f"{label}:enter")
                await asyncio.sleep(hold_for)
                order.append(f"{label}:exit")

        await asyncio.gather(task("A", 0.05), task("B", 0.0))

        # Whichever entered first must finish before the other enters.
        assert order in (
            ["A:enter", "A:exit", "B:enter", "B:exit"],
            ["B:enter", "B:exit", "A:enter", "A:exit"],
        ), order

    async def test_distinct_ids_do_not_block(self) -> None:
        manager = InMemoryConversationLockManager()
        order: list[str] = []

        async def hold(conv_id: str, label: str, hold_for: float) -> None:
            async with manager.acquire(conv_id):
                order.append(f"{label}:enter")
                await asyncio.sleep(hold_for)
                order.append(f"{label}:exit")

        await asyncio.gather(
            hold("conv-1", "A", 0.03),
            hold("conv-2", "B", 0.0),
        )

        # B should finish entirely while A still holds its own lock.
        assert order.index("B:exit") < order.index("A:exit")

    async def test_empty_conversation_id_not_serialized(self) -> None:
        manager = InMemoryConversationLockManager()
        order: list[str] = []

        async def hold(label: str, hold_for: float) -> None:
            async with manager.acquire(""):
                order.append(f"{label}:enter")
                await asyncio.sleep(hold_for)
                order.append(f"{label}:exit")

        await asyncio.gather(hold("A", 0.03), hold("B", 0.0))

        # Both run in parallel — no serialization
        assert order.index("B:enter") < order.index("A:exit")

    async def test_locks_garbage_collected_after_release(self) -> None:
        manager = InMemoryConversationLockManager()
        async with manager.acquire("conv-1"):
            assert manager.active_lock_count == 1
        assert manager.active_lock_count == 0

    async def test_runtime_checkable_protocol(self) -> None:
        manager = InMemoryConversationLockManager()
        assert isinstance(manager, ConversationLockManager)


# ---------------------------------------------------------------------------
# 2. Service-level: prove the unlocked path drops turns (control case)
# ---------------------------------------------------------------------------


class _NoOpLockManager:
    """ConversationLockManager that grants every acquire instantly."""

    @asynccontextmanager
    async def acquire(self, conversation_id: str) -> AsyncIterator[None]:
        yield


def _make_paced_llm(reply: str, hold_for: float) -> MockLLMClient:
    """Build a mock LLM whose first chunk arrives only after `hold_for` seconds.

    Used to widen the load-mutate-save race window so an unlocked run loses
    a turn deterministically.
    """

    class _PacedMock(MockLLMClient):
        async def chat_stream(self, messages, *, tools=None, **kwargs):  # type: ignore[override]
            self.calls.append({
                "messages": [m.to_openai() if hasattr(m, "to_openai") else m for m in messages],
                "tools": tools,
                "stream": True,
                **kwargs,
            })
            await asyncio.sleep(hold_for)
            yield LLMChunk(content=reply, finish_reason="stop")

    return _PacedMock([LLMResponse(content=reply, finish_reason="stop")])


async def _run_concurrent_pair(
    *,
    db_path: Path,
    lock_manager: ConversationLockManager,
    delay_a: float,
    delay_b: float,
) -> tuple[str, str, list[dict]]:
    store = SqlitePersistence(db_path)
    llm_a = _make_paced_llm("reply-A", hold_for=delay_a)
    llm_b = _make_paced_llm("reply-B", hold_for=delay_b)

    async def call(req_id: str, query: str, llm: MockLLMClient) -> str:
        service = AgentOrchestrationService(
            llm_factory=lambda llm=llm: llm,
            persistence=store,
            lock_manager=lock_manager,
        )
        ctx = AgentContext(
            request_id=req_id,
            query=query,
            conversation_id="race-conv",
        )
        return await service.run(
            agent_name="general_chat",
            query=query,
            context=ctx,
        )

    res_a, res_b = await asyncio.gather(
        call("req-A", "ask A", llm_a),
        call("req-B", "ask B", llm_b),
    )

    final = await store.load_messages("race-conv")
    return res_a, res_b, final


class TestConversationRace:
    async def test_unlocked_path_loses_turns(self, tmp_path: Path) -> None:
        """Control case: without serialization, concurrent runs overwrite each other."""
        _, _, final = await _run_concurrent_pair(
            db_path=tmp_path / "race.db",
            lock_manager=_NoOpLockManager(),
            # A is slower, so B finishes its load → save first; A then saves
            # only its own view, dropping B's persisted turn.
            delay_a=0.1,
            delay_b=0.0,
        )

        user_messages = [m for m in final if m["role"] == "user"]
        assistant_messages = [m for m in final if m["role"] == "assistant"]

        # Without locking, fewer than 2 user/assistant pairs survive.
        # We assert at least one was dropped (usually exactly one).
        dropped = (len(user_messages) < 2) or (len(assistant_messages) < 2)
        assert dropped, (
            "expected at least one turn to be lost without lock, but DB looks intact: "
            f"users={len(user_messages)} asst={len(assistant_messages)}"
        )

    async def test_locked_path_preserves_both_turns(self, tmp_path: Path) -> None:
        """With the default lock manager, both turns must survive in order."""
        manager = InMemoryConversationLockManager()
        _, _, final = await _run_concurrent_pair(
            db_path=tmp_path / "race.db",
            lock_manager=manager,
            delay_a=0.1,
            delay_b=0.0,
        )

        user_messages = [m for m in final if m["role"] == "user"]
        assistant_messages = [m for m in final if m["role"] == "assistant"]

        # Both turns survived
        assert len(user_messages) == 2, [m["content"] for m in user_messages]
        assert len(assistant_messages) == 2

        # The assistant replies are the two distinct mock outputs
        contents = sorted(m["content"] for m in assistant_messages)
        assert contents == ["reply-A", "reply-B"]

        # Lock was released
        assert manager.active_lock_count == 0


# ---------------------------------------------------------------------------
# 3. Service: distinct conversations remain parallel
# ---------------------------------------------------------------------------


class TestDistinctConversationsRunInParallel:
    async def test_two_conversations_do_not_block_each_other(
        self,
        tmp_path: Path,
    ) -> None:
        store = SqlitePersistence(tmp_path / "parallel.db")
        manager = InMemoryConversationLockManager()

        timings: dict[str, float] = {}

        async def call(conv_id: str, hold: float) -> None:
            llm = _make_paced_llm(f"reply-{conv_id}", hold_for=hold)
            service = AgentOrchestrationService(
                llm_factory=lambda llm=llm: llm,
                persistence=store,
                lock_manager=manager,
            )
            ctx = AgentContext(
                request_id=f"req-{conv_id}",
                query="hi",
                conversation_id=conv_id,
            )
            start = asyncio.get_event_loop().time()
            await service.run(
                agent_name="general_chat",
                query="hi",
                context=ctx,
            )
            timings[conv_id] = asyncio.get_event_loop().time() - start

        # Both runs are paced 0.1s; if they ran serially total would be ~0.2s.
        start = asyncio.get_event_loop().time()
        await asyncio.gather(call("conv-1", 0.1), call("conv-2", 0.1))
        elapsed = asyncio.get_event_loop().time() - start

        # Parallel execution should take ~0.1s, not ~0.2s.
        assert elapsed < 0.18, (
            f"distinct conversations appear serialized: total {elapsed:.3f}s "
            f"per-call {timings}"
        )
