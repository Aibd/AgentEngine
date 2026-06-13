from app.backend.agents.preset import AgentDefinition

DEFINITION = AgentDefinition(
    name="general_chat",
    description="Simple chat agent with no special business workflow.",
    instructions="You are a helpful assistant. Answer the user directly.",
)
