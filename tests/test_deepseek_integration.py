from __future__ import annotations

import os

import pytest

from agent_core.llm.factory import create_llm_from_env
from services.agent_orchestration_service import AgentOrchestrationService


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to call the real LLM provider.",
)
async def test_deepseek_general_chat_smoke():
    llm = create_llm_from_env()
    assert llm is not None

    try:
        result = await AgentOrchestrationService(
            config_path="config/agents.yaml",
            llm_factory=lambda: llm,
        ).run(
            agent_name="general_chat",
            query="Reply with exactly: DeepSeek integration test ok.",
        )
    finally:
        await llm.close()

    assert result.strip()
