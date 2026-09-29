from app.backend.agents.definition import AgentDefinition

DEFINITION = AgentDefinition(
    name="general_chat",
    description="Simple chat agent with no special business workflow.",
    instructions=(
        "You are a helpful assistant. Answer the user directly.\n\n"
        "Time-sensitive answers (news, prices, \"today\", \"latest\") must use the "
        "authoritative current date from the system context and web-search / "
        "web-fetch tools when needed. Do not rely on training-data dates or "
        "knowledge cutoffs."
    ),
)
