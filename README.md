# Agent Core Refactor

Standalone scaffold for the Agent module refactor. Lives outside the existing
`gjsk_wiseagent_ai` project so the new core can evolve without breaking
FileClerk or current deep research behavior.

## Layout

```
src/
  agent_core/    framework code, no business service imports
    base/        BaseAgent (data container) + AgentContext + AgentState
    handlers/    ReActHandler / PipelineHandler / LegacyHandler
    llm/         OpenAI-compatible client, Mock client, LangChain adapter
    memory/      Message + Memory (with trim + multimodal)
    prompts/     PromptLoader (cached YAML)
    registry/    decorator-based agent + handler registries
    stream/      EventStream + Printer (full SSE envelope)
    tools/       Tool / ToolCollection / Registry / PlanningTool
  agents/        business agent declarations + legacy adapters
  services/      application-facing entry point
config/agents.yaml  agent enablement and default handler/model config
tests/         pytest suite covering every framework module
```

## Architecture decisions

### Loop logic lives in handlers, not agents

`BaseAgent` is a pure context container holding `memory`, `state`, `current_step`,
and three hooks subclasses can override:

- `setup()`: register tools, seed memory, inject extras
- `system_prompt()`: return the system prompt string
- `next_step_prompt()`: optional per-turn guidance

The actual think→act loop runs inside `ReActHandler` (or `PipelineHandler`,
`LegacyHandler`). This keeps agents declarative and lets configuration
(`agents.yaml`) decide which loop pattern any given agent uses without
inheritance gymnastics.

### Tool calls flow as raw OpenAI dicts end-to-end

`LLMResponse.tool_calls` and `Message.tool_calls` both carry the OpenAI raw
dict shape (`{id, type, function: {name, arguments}}`). No `ToolCall` dataclass
in between. This means the assistant message written into memory can be sent
back to the LLM verbatim on the next turn.

### Streaming tool_calls are accumulated by index

`OpenAICompatibleClient._collect_stream` rebuilds full `tool_calls` from
`delta.tool_calls[*].function.arguments` chunks before returning. Handlers
never see partial tool calls.

### Printer envelope matches the existing SSE contract

`Printer` emits dicts with `responseType / response / responseAll / useTimes /
reqId / errorMsg / resultMap / conversation_id / finished` so frontends do
not need to change.

## Migration rule

Do not move or delete legacy code first. Add adapters and compatibility
handlers, then switch callers after contract tests pass.

Recommended first integration path:

1. Keep existing `PlanSolveHandlerImpl` reachable via `LegacyHandler`.
2. Register new agents (`general_chat`, `deep_research`) beside it.
3. Wrap FileClerk through `FileClerkAdapter`; do not rewrite FileClerk
   internals in this phase.
4. SSE field stability is enforced by `Printer` itself.

## Running tests

```bash
pip install -e .[dev]
pytest
```

`pyproject.toml` sets `pythonpath = ["src"]` and `asyncio_mode = "auto"` so
no extra config is needed.
