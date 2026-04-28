# Agent Core Refactor

Standalone scaffold for the Agent module refactor. It is intentionally outside the existing `gjsk_wiseagent_ai` project so the new core can evolve without breaking FileClerk or current deep research behavior.

## Layout

- `src/agent_core/`: reusable framework code, no business service imports.
- `src/agents/`: business agent declarations and legacy adapters.
- `src/services/agent_orchestration_service.py`: application-facing entry point.
- `config/agents.yaml`: agent enablement and default handler/model config.

## Migration Rule

Do not move or delete legacy code first. Add adapters and compatibility handlers, then switch callers after contract tests pass.

Recommended first integration path:

1. Keep existing `PlanSolveHandlerImpl` as `plan_solve_legacy`.
2. Register new agents beside it.
3. Keep current SSE fields stable: `responseType`, `response`, `responseAll`, `resultMap`, `conversation_id`, `finished`.
4. Wrap FileClerk through `FileClerkAdapter`; do not rewrite FileClerk internals in this phase.
