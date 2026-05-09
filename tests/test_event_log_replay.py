from __future__ import annotations

import logging

from agentengine.observability import event_log as event_log_module
from agentengine.observability.event_log import RunEventLog
from agentengine.runtime.events import RunCompleted, RunStarted, TextDelta


def test_event_log_replays_jsonl_records(tmp_path) -> None:
    log = RunEventLog("run_1", base_dir=tmp_path)

    events = [
        RunStarted(run_id="run_1", turn_id="turn_1", agent_name="agent"),
        TextDelta(run_id="run_1", turn_id="turn_1", content="hello"),
        RunCompleted(run_id="run_1", turn_id="turn_1", result_summary="done"),
    ]
    for event in events:
        log.append(event)

    records = log.read_records()

    assert [record["event_type"] for record in records] == [
        "run_started",
        "text_delta",
        "run_completed",
    ]
    assert records[0]["run_id"] == "run_1"


def test_event_log_truncates_when_existing_file_is_too_large(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(event_log_module, "_MAX_SIZE_BYTES", 10)
    caplog.set_level(logging.WARNING)
    log = RunEventLog("run_1", base_dir=tmp_path)

    log.append(TextDelta(run_id="run_1", turn_id="turn_1", content="x" * 100))
    log.append(TextDelta(run_id="run_1", turn_id="turn_1", content="ignored"))

    assert not log.path.exists()
    assert log.path.with_suffix(".jsonl.truncated").exists()
    assert "run event log truncated" in caplog.text
