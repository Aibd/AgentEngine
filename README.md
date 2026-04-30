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
    llm/         OpenAI-compatible client, mock client contract, env factory
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

### Runtime events are internal diagnostics

`TurnRunner` records semantic runtime events separately from the public SSE
stream. `RuntimeEvent` objects live under `agent_core.runtime.events`; SSE
protocol names live under `agent_core.stream.events.EventType`. Keep these
layers separate so the internal lifecycle model can evolve without changing
front-end contracts.

Set `USE_LEGACY_RUNNER=true` to bypass `TurnRunner` and use the original
service-to-handler path during rollout or incident recovery:

```powershell
$env:USE_LEGACY_RUNNER='true'
uv run --extra dev pytest -q
Remove-Item Env:\USE_LEGACY_RUNNER
```

### Run event logs

Runtime events are appended to jsonl files under:

```text
${AGENT_CORE_LOG_DIR:-logs}/runs/<YYYY-MM-DD>/<run_id>.jsonl
```

Each line is one serialized runtime event with `event_type`, `run_id`,
`turn_id`, and a timestamp. For local debugging, inspect the file referenced by
`context.extras["run_event_log_path"]`. If a provider reports a context-window
failure, it should be represented as `LLMContextWindowError`; `TurnRunner`
records that as `terminal_reason="context_exceeded"` so later planning can use
real data before adding any TokenBudget or compaction layer.

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
uv sync --extra dev
uv run --env-file .env pytest
```

`pyproject.toml` sets `pythonpath = ["src"]` and `asyncio_mode = "auto"` so
no extra config is needed.

Real provider smoke tests are opt-in:

```bash
# Set RUN_INTEGRATION=1 in .env first.
uv run --env-file .env pytest -m integration
```

## API documentation

See `docs/API.md` for the public contracts around agent lifecycle, LLM clients,
structured errors, streaming events, registries, memory, and orchestration.
