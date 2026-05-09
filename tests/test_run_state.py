from __future__ import annotations

from agentengine.runtime.run_state import RunState, RunStatus, TerminalReason


def test_run_state_transitions_to_completed() -> None:
    state = RunState(run_id="run_1", session_id="session_1", turn_id="turn_1")

    state.mark_running()
    assert state.status == RunStatus.RUNNING
    assert state.started_at is not None

    state.mark_completed()
    assert state.status == RunStatus.COMPLETED
    assert state.terminal_reason == TerminalReason.NORMAL
    assert state.ended_at is not None


def test_run_state_transitions_to_failed() -> None:
    state = RunState(run_id="run_1", session_id="session_1", turn_id="turn_1")

    state.mark_running()
    state.mark_failed(TerminalReason.MODEL_FAILED)

    assert state.status == RunStatus.FAILED
    assert state.terminal_reason == TerminalReason.MODEL_FAILED


def test_run_state_transitions_to_cancelled() -> None:
    state = RunState(run_id="run_1", session_id="session_1", turn_id="turn_1")

    state.mark_running()
    state.mark_cancelled()

    assert state.status == RunStatus.CANCELLED
    assert state.terminal_reason == TerminalReason.CANCELLED
