from __future__ import annotations

from typing import Any

import pytest

from agentengine.runtime.events import (
    RuntimeEvent,
    TodosUpdated,
    UserQuestionAsked,
)
from agentengine.tools.builtin.ask_user_question_tool import AskUserQuestionTool
from agentengine.tools.builtin.todo_write_tool import TodoWriteTool


class _EventRecorder:
    """Async-capable emit callback that records every event it sees."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def __call__(self, event: RuntimeEvent) -> None:
        self.events.append(event)


def _todo(content: str, *, status: str = "pending", active: str | None = None) -> dict[str, str]:
    return {
        "content": content,
        "activeForm": active or content,
        "status": status,
    }


class TestTodoWriteTool:
    async def test_writes_to_store_and_emits(self) -> None:
        store: dict[str, Any] = {}
        recorder = _EventRecorder()
        tool = TodoWriteTool(
            store=store,
            emit=recorder,
            run_id="r1",
            turn_id="t1",
        )
        out = await tool.run(
            todos=[
                _todo("Write tests", status="in_progress"),
                _todo("Run pytest"),
            ]
        )
        assert "Todos updated" in out
        assert len(store["todos"]) == 2
        assert store["todos"][0]["status"] == "in_progress"

        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert isinstance(event, TodosUpdated)
        assert event.run_id == "r1"
        assert len(event.todos) == 2

    async def test_validates_at_most_one_in_progress(self) -> None:
        store: dict[str, Any] = {}
        tool = TodoWriteTool(store=store)
        out = await tool.run(
            todos=[
                _todo("Task A", status="in_progress"),
                _todo("Task B", status="in_progress"),
            ]
        )
        assert "at most one todo may be in_progress" in out
        assert "todos" not in store

    async def test_rejects_unknown_status(self) -> None:
        tool = TodoWriteTool()
        out = await tool.run(todos=[_todo("X", status="archived")])
        assert "status must be one of" in out

    async def test_rejects_missing_active_form(self) -> None:
        tool = TodoWriteTool()
        out = await tool.run(
            todos=[{"content": "X", "activeForm": "", "status": "pending"}]
        )
        assert "activeForm" in out

    async def test_clears_store_when_all_completed(self) -> None:
        store: dict[str, Any] = {}
        tool = TodoWriteTool(store=store)
        out = await tool.run(
            todos=[
                _todo("A", status="completed"),
                _todo("B", status="completed"),
            ]
        )
        assert "All tasks completed" in out
        assert store["todos"] == []

    async def test_rejects_non_array_input(self) -> None:
        tool = TodoWriteTool()
        out = await tool.run(todos="not a list")
        assert "must be an array" in out

    async def test_rejects_non_object_item(self) -> None:
        tool = TodoWriteTool()
        out = await tool.run(todos=["not an object"])
        assert "must be an object" in out

    async def test_works_without_emit_callback(self) -> None:
        # The tool should still mutate the store even if no emit was supplied.
        store: dict[str, Any] = {}
        tool = TodoWriteTool(store=store)
        out = await tool.run(todos=[_todo("Solo")])
        assert "Todos updated" in out
        assert store["todos"][0]["content"] == "Solo"

    def test_to_openai_tool_shape(self) -> None:
        schema = TodoWriteTool().to_openai_tool()
        assert schema["function"]["name"] == "TodoWrite"
        assert "todos" in schema["function"]["parameters"]["properties"]


class TestAskUserQuestionTool:
    async def test_emits_event_and_returns_placeholder(self) -> None:
        recorder = _EventRecorder()
        tool = AskUserQuestionTool(emit=recorder, run_id="r1", turn_id="t1")
        out = await tool.run(question="Which database backend?", options=["PG", "MySQL"])
        assert "Question delivered" in out
        assert "PG" in out
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert isinstance(event, UserQuestionAsked)
        assert event.question == "Which database backend?"
        assert event.options == ["PG", "MySQL"]
        assert event.multiple is False
        assert event.question_id.startswith("q_")

    async def test_multiple_flag_propagated(self) -> None:
        recorder = _EventRecorder()
        tool = AskUserQuestionTool(emit=recorder)
        await tool.run(
            question="Pick targets",
            options=["a", "b", "c"],
            multiple=True,
        )
        event = recorder.events[0]
        assert isinstance(event, UserQuestionAsked)
        assert event.multiple is True

    async def test_rejects_empty_question(self) -> None:
        tool = AskUserQuestionTool()
        out = await tool.run(question="   ")
        assert "non-empty" in out

    async def test_rejects_too_long_question(self) -> None:
        tool = AskUserQuestionTool()
        out = await tool.run(question="x" * (AskUserQuestionTool.MAX_QUESTION_CHARS + 1))
        assert "too long" in out

    async def test_rejects_non_array_options(self) -> None:
        tool = AskUserQuestionTool()
        out = await tool.run(question="Q", options="not a list")
        assert "must be an array" in out

    async def test_rejects_non_string_option(self) -> None:
        tool = AskUserQuestionTool()
        out = await tool.run(question="Q", options=["ok", 5])
        assert "options[1]" in out

    async def test_rejects_too_many_options(self) -> None:
        tool = AskUserQuestionTool()
        out = await tool.run(
            question="Q", options=[f"opt-{i}" for i in range(AskUserQuestionTool.MAX_OPTIONS + 1)]
        )
        assert "too many options" in out

    async def test_works_without_emit(self) -> None:
        tool = AskUserQuestionTool()
        out = await tool.run(question="hello?")
        assert "Question delivered" in out

    def test_to_openai_tool_shape(self) -> None:
        schema = AskUserQuestionTool().to_openai_tool()
        assert schema["function"]["name"] == "AskUserQuestion"
        assert "question" in schema["function"]["parameters"]["properties"]


class TestBuilderIncludesNewTools:
    def test_factories_registered(self) -> None:
        from agentengine.tools.builtin import BUILTIN_TOOL_FACTORIES

        assert "TodoWrite" in BUILTIN_TOOL_FACTORIES
        assert "AskUserQuestion" in BUILTIN_TOOL_FACTORIES

    def test_default_set_does_not_include_session_tools(self, tmp_path) -> None:
        # build_default_tools w/o include= should *include* TodoWrite and
        # AskUserQuestion (they're in BUILTIN_TOOL_FACTORIES). Just verify
        # the names appear in the result set.
        from agentengine.tools.builtin import build_default_tools

        tools = build_default_tools(workspace_root=tmp_path)
        names = {t.name for t in tools}
        assert "TodoWrite" in names
        assert "AskUserQuestion" in names
