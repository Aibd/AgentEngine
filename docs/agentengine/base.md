# Base

`src/agentengine/base/` contains the mutable per-run objects:

- `AgentRun`: runtime state, memory, current turn counter, lifecycle state
- `AgentContext`: request dependencies such as LLM, tools, persistence, user,
  printer, and `extras`
- `AgentState`: `IDLE`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELLED`

Reusable execution settings live in `RunConfig`, not on `AgentRun`.

## RunConfig

`RunConfig` is immutable and contains:

- `name`
- `initial_messages`
- `max_messages`
- `auto_compact_tokens`
- `compaction_keep_recent`
- optional `compactor`
- `setup` / `teardown`
- `extras`

`max_steps` / `max_turns` have been removed. Stop behavior belongs to runtime
signals: natural model completion, hooks, compaction errors, cancellation, and
provider/runtime errors.
