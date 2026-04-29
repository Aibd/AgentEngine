from __future__ import annotations

import pytest

from agent_core.base.agent import BaseAgent
from agent_core.base.context import AgentContext
from agent_core.registry.agent_registry import (
    create_agent,
    get_agent_handler,
    register_agent,
    registered_agents,
)
from agent_core.registry.handler_registry import (
    create_handler,
    register_handler,
    registered_handlers,
)


class TestAgentRegistry:
    def test_register_and_create(self, agent_context: AgentContext):
        @register_agent("registry_test_agent", handler="react")
        class _A(BaseAgent):
            description = "x"

        assert "registry_test_agent" in registered_agents()
        assert get_agent_handler("registry_test_agent") == "react"

        agent = create_agent("registry_test_agent", agent_context)
        assert agent.name == "registry_test_agent"

    def test_create_unknown_raises(self, agent_context: AgentContext):
        with pytest.raises(KeyError):
            create_agent("nope_agent", agent_context)

    def test_default_handler_when_unknown(self):
        # Unknown agent name returns "react" default
        assert get_agent_handler("not_registered_anywhere") == "react"


class TestHandlerRegistry:
    def test_register_and_create(self):
        @register_handler("registry_test_handler")
        class _H:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def handle(self, agent, context, query):
                return "handled"

        assert "registry_test_handler" in registered_handlers()
        h = create_handler("registry_test_handler", extra=42)
        assert h.kwargs == {"extra": 42}

    def test_create_unknown_raises(self):
        with pytest.raises(KeyError):
            create_handler("nope_handler")
