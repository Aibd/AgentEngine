from __future__ import annotations

import pytest

from agentengine.hooks import (
    HookAbortError,
    HookEvent,
    HookManager,
    HookOutcome,
    HookResult,
    PreToolUsePayload,
    SessionStartPayload,
)


def _payload(**overrides: object) -> SessionStartPayload:
    base: dict[str, object] = {
        "session_id": "s1",
        "run_id": "r1",
        "turn_id": "t1",
        "cwd": "",
        "agent_name": "test",
        "query_summary": "hi",
    }
    base.update(overrides)
    return SessionStartPayload(**base)  # type: ignore[arg-type]


class TestRegistration:
    def test_register_and_count(self) -> None:
        manager = HookManager()
        assert manager.count(HookEvent.SESSION_START) == 0
        manager.register(HookEvent.SESSION_START, lambda _: HookResult.success(), name="a")
        assert manager.count(HookEvent.SESSION_START) == 1

    def test_decorator_form(self) -> None:
        manager = HookManager()

        @manager.on(HookEvent.PRE_TOOL_USE)
        async def _hook(payload):  # type: ignore[no-untyped-def]
            return HookResult.success()

        assert manager.count(HookEvent.PRE_TOOL_USE) == 1
        names = manager.snapshot()["PreToolUse"]
        assert "_hook" in names

    def test_unregister(self) -> None:
        manager = HookManager()

        async def fn(payload):  # type: ignore[no-untyped-def]
            return HookResult.success()

        manager.register(HookEvent.STOP, fn)
        assert manager.unregister(HookEvent.STOP, fn)
        assert manager.count(HookEvent.STOP) == 0

    def test_clear(self) -> None:
        manager = HookManager()
        manager.register(HookEvent.STOP, lambda _: HookResult.success(), name="a")
        manager.register(HookEvent.SESSION_START, lambda _: HookResult.success(), name="b")
        manager.clear(HookEvent.STOP)
        assert manager.count(HookEvent.STOP) == 0
        assert manager.count(HookEvent.SESSION_START) == 1
        manager.clear()
        assert manager.count(HookEvent.SESSION_START) == 0


class TestDispatch:
    async def test_no_handlers_returns_empty(self) -> None:
        manager = HookManager()
        results = await manager.dispatch(HookEvent.STOP, _payload())
        assert results == []

    async def test_runs_in_registration_order(self) -> None:
        manager = HookManager()
        order: list[str] = []

        async def first(payload):  # type: ignore[no-untyped-def]
            order.append("first")
            return HookResult.success()

        async def second(payload):  # type: ignore[no-untyped-def]
            order.append("second")
            return HookResult.success()

        manager.register(HookEvent.SESSION_START, first)
        manager.register(HookEvent.SESSION_START, second)
        results = await manager.dispatch(HookEvent.SESSION_START, _payload())
        assert order == ["first", "second"]
        assert all(r.outcome is HookOutcome.SUCCESS for r in results)

    async def test_sync_handler_supported(self) -> None:
        manager = HookManager()
        manager.register(
            HookEvent.STOP,
            lambda payload: HookResult.success(),
            name="sync",
        )
        results = await manager.dispatch(HookEvent.STOP, _payload())
        assert results[0].outcome is HookOutcome.SUCCESS

    async def test_none_return_treated_as_success(self) -> None:
        manager = HookManager()

        async def fn(payload):  # type: ignore[no-untyped-def]
            return None

        manager.register(HookEvent.STOP, fn)
        results = await manager.dispatch(HookEvent.STOP, _payload())
        assert results[0].outcome is HookOutcome.SUCCESS

    async def test_fail_continue_does_not_abort(self) -> None:
        manager = HookManager()
        order: list[str] = []

        async def first(payload):  # type: ignore[no-untyped-def]
            order.append("first")
            return HookResult.fail_continue("ignored")

        async def second(payload):  # type: ignore[no-untyped-def]
            order.append("second")
            return HookResult.success()

        manager.register(HookEvent.SESSION_START, first)
        manager.register(HookEvent.SESSION_START, second)
        results = await manager.dispatch(HookEvent.SESSION_START, _payload())
        assert order == ["first", "second"]
        assert results[0].outcome is HookOutcome.FAIL_CONTINUE
        assert results[1].outcome is HookOutcome.SUCCESS

    async def test_fail_abort_raises_and_short_circuits(self) -> None:
        manager = HookManager()
        order: list[str] = []

        async def first(payload):  # type: ignore[no-untyped-def]
            order.append("first")
            return HookResult.fail_abort("nope")

        async def second(payload):  # type: ignore[no-untyped-def]
            order.append("second")
            return HookResult.success()

        manager.register(HookEvent.SESSION_START, first, name="first")
        manager.register(HookEvent.SESSION_START, second, name="second")
        with pytest.raises(HookAbortError) as exc:
            await manager.dispatch(HookEvent.SESSION_START, _payload())
        assert order == ["first"]
        assert exc.value.event is HookEvent.SESSION_START
        assert exc.value.handler_name == "first"
        assert exc.value.reason == "nope"

    async def test_handler_exception_becomes_fail_continue(self) -> None:
        manager = HookManager()

        async def crash(payload):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        async def follow(payload):  # type: ignore[no-untyped-def]
            return HookResult.success()

        manager.register(HookEvent.STOP, crash, name="crash")
        manager.register(HookEvent.STOP, follow, name="follow")
        results = await manager.dispatch(HookEvent.STOP, _payload())
        assert results[0].outcome is HookOutcome.FAIL_CONTINUE
        assert "RuntimeError" in results[0].reason
        assert "boom" in results[0].reason
        # second handler still runs because crash → fail_continue
        assert results[1].outcome is HookOutcome.SUCCESS

    async def test_payload_passed_through(self) -> None:
        manager = HookManager()
        seen: list[PreToolUsePayload] = []

        async def capture(payload):  # type: ignore[no-untyped-def]
            seen.append(payload)
            return HookResult.success()

        manager.register(HookEvent.PRE_TOOL_USE, capture)
        await manager.dispatch(
            HookEvent.PRE_TOOL_USE,
            PreToolUsePayload(
                session_id="s",
                run_id="r",
                turn_id="t",
                tool_name="bash",
                tool_call_id="tc1",
                arguments={"command": "ls"},
            ),
        )
        assert seen[0].tool_name == "bash"
        assert seen[0].arguments == {"command": "ls"}


class TestResultFactories:
    def test_success_default(self) -> None:
        r = HookResult.success()
        assert r.outcome is HookOutcome.SUCCESS
        assert r.should_abort is False

    def test_fail_continue(self) -> None:
        r = HookResult.fail_continue("policy")
        assert r.outcome is HookOutcome.FAIL_CONTINUE
        assert r.reason == "policy"
        assert r.should_abort is False

    def test_fail_abort(self) -> None:
        r = HookResult.fail_abort("blocked")
        assert r.outcome is HookOutcome.FAIL_ABORT
        assert r.should_abort is True
