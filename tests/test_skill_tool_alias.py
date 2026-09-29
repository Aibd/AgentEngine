"""Models often invent a top-level tool named after a skill (e.g. web-search).

These aliases must resolve to Skill / RunSkillScript instead of ToolNotFound.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentengine.runtime.turn import (
    _coerce_skill_script_args,
    _resolve_skill_tool_alias,
    _unknown_tool_message,
)
from agentengine.tools.base import Tool
from agentengine.tools.collection import ToolCollection


class _SkillToolStub(Tool):
    name = "Skill"
    description = "stub"
    schema = {"type": "object", "properties": {}}

    def __init__(self, skills: dict[str, object]) -> None:
        self._skills = skills

    def _enabled_skills(self) -> dict[str, object]:
        return self._skills

    async def run(self, **kwargs):
        return f"activated:{kwargs.get('skill')}"


class _RunSkillScriptStub(Tool):
    name = "RunSkillScript"
    description = "stub"
    schema = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        return f"ran:{kwargs.get('skill')}:{kwargs.get('script')}:{kwargs.get('args')}"


def _skill(name: str, scripts: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        resources=SimpleNamespace(scripts=scripts),
        base_dir=Path("."),
        host_exec=True,
    )


def test_alias_with_query_routes_to_run_skill_script() -> None:
    skill_tool = _SkillToolStub(
        {"web-search": _skill("web-search", ("scripts/search.py",))}
    )
    run_script = _RunSkillScriptStub()
    collection = ToolCollection([skill_tool, run_script])

    resolved = _resolve_skill_tool_alias(
        collection, "web-search", {"query": "今日 AI 新闻", "max_results": 5}
    )
    assert resolved is not None
    tool, args, name = resolved
    assert tool is run_script
    assert name == "RunSkillScript"
    assert args == {
        "skill": "web-search",
        "script": "scripts/search.py",
        "args": ["今日 AI 新闻", "5"],
    }


def test_alias_without_payload_activates_skill() -> None:
    skill_tool = _SkillToolStub(
        {"web-search": _skill("web-search", ("scripts/search.py",))}
    )
    run_script = _RunSkillScriptStub()
    collection = ToolCollection([skill_tool, run_script])

    resolved = _resolve_skill_tool_alias(collection, "web-search", {})
    assert resolved is not None
    tool, args, name = resolved
    assert tool is skill_tool
    assert name == "Skill"
    assert args == {"skill": "web-search", "args": ""}


def test_unknown_skill_name_is_not_aliased() -> None:
    skill_tool = _SkillToolStub(
        {"web-search": _skill("web-search", ("scripts/search.py",))}
    )
    collection = ToolCollection([skill_tool])
    assert _resolve_skill_tool_alias(collection, "nope", {"query": "x"}) is None


def test_coerce_skill_script_args_shapes() -> None:
    assert _coerce_skill_script_args({"args": ["a", "b"]}) == ["a", "b"]
    assert _coerce_skill_script_args({"query": "hello", "max_results": 3}) == [
        "hello",
        "3",
    ]
    assert _coerce_skill_script_args({"url": "https://example.com"}) == [
        "https://example.com"
    ]
    assert _coerce_skill_script_args({}) is None


def test_unknown_tool_message_mentions_skill_path() -> None:
    collection = ToolCollection([_SkillToolStub({})])
    msg = _unknown_tool_message("web-search", collection)
    assert "Unknown tool: web-search" in msg
    assert "Skill" in msg
    assert "Available tools: Skill" in msg
