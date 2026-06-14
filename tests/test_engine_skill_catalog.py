"""Tests for AgentEngine skill catalog injection."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentengine import AgentContext, AgentEngine
from agentengine.base.agent import AgentRun
from agentengine.base.state import AgentState
from agentengine.memory.memory import Memory
from agentengine.run_config import RunConfig
from agentengine.skills.loader import SkillLoader


def _write_skill(root: Path, dirname: str, content: str) -> Path:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8", newline="")
    return skill_file


def test_enable_skill_catalog_requires_skill_loader() -> None:
    with pytest.raises(ValueError, match="skill_loader"):
        AgentEngine(enable_skill_catalog=True)


def test_injects_catalog_system_message(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "demo", "---\nname: demo\ndescription: A demo skill\n---\nBody")

    engine = AgentEngine(
        require_llm=False,
        skill_loader=SkillLoader(roots=[root]),
        enable_skill_catalog=True,
    )
    context = AgentContext(request_id="r1", query="hello")
    config = RunConfig(name="test")
    agent = AgentRun(config=config, context=context)

    engine._inject_skill_catalog(agent)

    system_messages = [m for m in agent.memory.messages if m.role.value == "system"]
    assert len(system_messages) == 1
    assert "<available_skills>" in system_messages[0].content
    assert "demo" in system_messages[0].content


def test_inject_catalog_is_noop_when_no_skills(tmp_path: Path) -> None:
    engine = AgentEngine(
        require_llm=False,
        skill_loader=SkillLoader(roots=[tmp_path / "empty"]),
        enable_skill_catalog=True,
    )
    context = AgentContext(request_id="r1", query="hello")
    config = RunConfig(name="test")
    agent = AgentRun(config=config, context=context)

    engine._inject_skill_catalog(agent)

    assert len(agent.memory.messages) == 0
