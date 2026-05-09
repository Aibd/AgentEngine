# hooks — 生命周期钩子

> `src/agentengine/hooks/` 提供可插拔的生命周期钩子系统，让外部代码可以在 Agent 执行的关键节点插入自定义逻辑。

---

## 模块组成

```
hooks/
├── manager.py    # HookManager — 注册与分发
├── types.py      # HookEvent / HookPayload / HookResult
└── __init__.py
```

---

## 设计意图

Hook 与 Middleware 的区别：

| 维度 | Hook | Middleware |
|------|------|-----------|
| 粒度 | 事件驱动（某事发生时） | 包装整个调用 |
| 用途 | 拦截、审计、扩展 | 横切关注点（配额、重试） |
| 返回值 | `HookResult`（继续/中止） | 直接返回结果或抛异常 |
| 组合 | 多 handler 顺序执行 | 洋葱式嵌套 |

Hook 适合「在某件事发生时做点什么事」，Middleware 适合「在整个调用前后包装一层」。

---

## HookEvent — 生命周期点

**文件：** `src/agentengine/hooks/types.py`

```python
class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"           # 工具执行前
    POST_TOOL_USE = "PostToolUse"         # 工具执行后
    SESSION_START = "SessionStart"        # 运行开始时
    USER_PROMPT_SUBMIT = "UserPromptSubmit"  # 用户提示词提交前
    STOP = "Stop"                         # 运行结束时
```

### 触发时机

```
TurnRunner.run()
    │
    ├──► SESSION_START              # 最早触发，可阻止整个运行
    │
    ├──► run_turn()
    │       │
    │       ├──► USER_PROMPT_SUBMIT  # 用户 query 加入 memory 前
    │       │
    │       ├──► 循环中...
    │       │       │
    │       │       ├──► PRE_TOOL_USE   # 工具执行前，可阻止该工具
    │       │       │   └──► tool.run()
    │       │       └──► POST_TOOL_USE  # 工具执行后，不可撤销
    │       │
    │       └──► 循环结束
    │
    └──► STOP                       # 运行收尾，用于清理
```

---

## HookResult — handler 的返回值

```python
class HookOutcome(str, Enum):
    SUCCESS = "success"           # 正常通过，继续下一个 handler
    FAIL_CONTINUE = "fail_continue"  # 软失败，记录但继续
    FAIL_ABORT = "fail_abort"     # 中止当前操作

class HookResult:
    outcome: HookOutcome
    reason: str = ""

    @classmethod
    def success(cls): ...
    @classmethod
    def fail_continue(cls, reason=""): ...
    @classmethod
    def fail_abort(cls, reason=""): ...
```

---

## HookManager — 注册与分发

**文件：** `src/agentengine/hooks/manager.py`

### 注册 handler

```python
from agentengine.hooks import HookManager, HookEvent, HookResult

manager = HookManager()

# 方式 1：直接注册
manager.register(HookEvent.PRE_TOOL_USE, my_handler, name="security_check")

# 方式 2：装饰器
@manager.on(HookEvent.POST_TOOL_USE, name="audit_logger")
def audit(payload):
    log.info("Tool used: %s", payload.tool_name)
    return HookResult.success()
```

### dispatch — 分发执行

```python
results = await manager.dispatch(HookEvent.PRE_TOOL_USE, payload)
```

- handler 按注册顺序执行
- 第一个返回 `FAIL_ABORT` 的 handler 会停止后续 handler，并引发 `HookAbortError`
- handler 抛异常会被捕获，转换为 `FAIL_CONTINUE`，**不会挂掉运行**

---

## Payload 类型

| HookEvent | Payload | 可用字段 |
|-----------|---------|---------|
| `SESSION_START` | `SessionStartPayload` | `session_id`, `run_id`, `turn_id`, `agent_name`, `query_summary` |
| `USER_PROMPT_SUBMIT` | `UserPromptSubmitPayload` | `session_id`, `run_id`, `turn_id`, `agent_name`, `query` |
| `PRE_TOOL_USE` | `PreToolUsePayload` | `tool_name`, `tool_call_id`, `arguments` |
| `POST_TOOL_USE` | `PostToolUsePayload` | `tool_name`, `tool_call_id`, `arguments`, `ok`, `result_summary`, `error_type`, `elapsed_seconds` |
| `STOP` | `StopPayload` | `status`, `elapsed_seconds` |

所有 payload 都继承 `_BasePayload`，包含 `session_id`, `run_id`, `turn_id`, `cwd`, `triggered_at`。

---

## 完整示例：安全拦截

```python
from agentengine.hooks import HookManager, HookEvent, HookResult, PreToolUsePayload

manager = HookManager()

@manager.on(HookEvent.PRE_TOOL_USE, name="forbid_rm")
def forbid_rm(payload: PreToolUsePayload):
    if payload.tool_name == "bash":
        cmd = payload.arguments.get("command", "")
        if "rm -rf /" in cmd:
            return HookResult.fail_abort(reason="Forbidden command detected")
    return HookResult.success()

# 注入到运行上下文
context.extras["hooks"] = manager
```

---

## 全局默认 Manager

```python
from agentengine.hooks import get_default_manager

# 获取进程级默认 HookManager
manager = get_default_manager()
manager.register(HookEvent.STOP, cleanup_handler)
```

---

## 关联文档

- [runtime.md](runtime.md) — run_turn 和 TurnRunner 何时触发 Hook
- [enterprise.md](enterprise.md) — Middleware 与 Hook 的对比
