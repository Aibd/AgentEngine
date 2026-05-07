from agent_core.spec import AgentSpec

SPEC = AgentSpec(
    name="general_chat",
    description="Simple chat agent with no special business workflow.",
    system_prompt="You are a helpful assistant. Answer the user directly.",
    max_steps=3,
)
