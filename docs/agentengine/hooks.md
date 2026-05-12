# Hooks

Hooks let host applications inspect or influence lifecycle points without
forking the runtime loop.

## Events

- `SessionStart`: before any LLM cost is incurred
- `UserPromptSubmit`: before the user query enters memory
- `PreToolUse`: before a tool runs; `HookResult.fail_abort()` skips the tool
- `PostToolUse`: after a tool succeeds or fails
- `AfterTurn`: after one think/act iteration; `HookResult.stop()` ends the loop
- `Stop`: terminal cleanup hook after success, failure, or cancellation

## Stop Example

```python
from agentengine.hooks import AfterTurnPayload, HookEvent, HookManager, HookResult

hooks = HookManager()


@hooks.on(HookEvent.AFTER_TURN, name="weak_model_guard")
async def stop_after_domain_signal(payload: AfterTurnPayload) -> HookResult:
    if payload.turn >= 3 and payload.has_tool_calls:
        return HookResult.stop("domain stop condition reached")
    return HookResult.success()


context.extras["hooks"] = hooks
```

`Stop` is still a terminal cleanup hook. Use `AfterTurn` for loop control.
