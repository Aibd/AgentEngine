# Agent Core 重构计划 — 走向企业级

> 状态:**已与用户对齐方向,等待开始执行 Phase 0**
> 制定日期:2026-05-07
> 目标版本:`agent-core` v0.2(破坏式升级,不向后兼容 v0.1)

---

## 0. 为什么要重构

当前实现(v0.1)被历史包袱误导,做出了一个**过度抽象的 N×M 策略矩阵**:N 种 Agent 子类 × M 种 Handler 实现。这与两个成熟参考系的设计哲学相反:

| | codex | claude-code-src | 当前 v0.1 |
|---|---|---|---|
| Agent 形态 | TOML 配置(`Role`) | `QueryParams` + markdown frontmatter | `BaseAgent` 类继承 |
| Loop 形态 | `tasks/regular.rs` 单一函数 | `query()` async generator | 3 种 `Handler` 子类 |
| 变化通过什么表达 | **数据** | **数据** | **类型/继承** |

**核心结论:loop 只有一种,变化通过配置/数据表达,不通过子类。**

v0.1 的具体技术债清单:

1. `LegacyHandler` + `legacy_factory` + `legacy_agent_type_map` + `USE_LEGACY_RUNNER` 后门 — 为兼容旧代码预留的钩子
2. `PipelineHandler` — 实际未被任何线上 Agent 使用的策略类型
3. `BaseAgent` 继承体系 — Agent 是"声明",不应该是类
4. `EventStream`(SSE)与 `RuntimeEvent`(内部)双轨事件模型 — 重复维护
5. `Printer` 信封中的 `responseAll` / `useTimes` / `responseType` — 兼容老前端的字段
6. `@register_agent` 装饰器 — 导入即副作用,反模式
7. 无多租户、无 OTel、无配额、无审批工作流 — 企业必备能力缺失

---

## 1. 目标架构(v0.2)

```
┌──────────────────────────────────────────────────────────┐
│  AgentSpec  (dataclass,无继承)                           │
│  ─ name, system_prompt, model, max_steps, tools_factory  │
│  ─ setup: Callable[[AgentContext], Awaitable[None]]      │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│  run_turn(spec, context, query) -> AsyncIterator[Event]  │
│  ─ 唯一的 think→act 循环(替代 ReActHandler)               │
│  ─ 不存在 Handler 多态                                     │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
                       RuntimeEvent
                  (统一的内部事件模型)
                  /         |          \
                 ▼          ▼           ▼
              SSE v2    JSONL Log   OTel Span
            (前端 sink) (审计 sink)  (监控 sink)
```

显式注册表(替代装饰器):
```python
# src/agents/__init__.py
REGISTRY: dict[str, AgentSpec] = {
    "general_chat": GENERAL_CHAT,
    "deep_research": DEEP_RESEARCH,
    "file_clerk": FILE_CLERK,
}
```

企业级中间件(横切关注点,在 `run_turn` 外层包装):
- TenantContext(多租户隔离)
- QuotaMiddleware(per-tenant / per-tool 限流)
- ApprovalGate(工具 `is_destructive=True` 时强制审批)
- OTelTracer(分布式追踪)
- SecretsBackend(替换 `.env` 直读)

---

## 2. 不再需要的清单(将被删除)

| 文件 / 概念 | 行数 | 命运 |
|---|---|---|
| `src/agent_core/handlers/legacy.py` | 88 | 删 |
| `src/agent_core/handlers/pipeline.py` | 88 | 删 |
| `src/agent_core/handlers/base.py` | 12 | 删 |
| `src/agent_core/handlers/react.py` | 425 | 重写为 `runtime/turn.py:run_turn()` 函数 |
| `src/agents/adapters/file_clerk_adapter.py` | 88 | 删 + 重写为 `agents/file_clerk/spec.py` |
| `src/agent_core/registry/` 装饰器 | — | 改为显式 dict |
| `BaseAgent`(`src/agent_core/base/agent.py`) | — | 替换为 `AgentSpec` dataclass |
| `tests/test_pipeline_legacy_handlers.py` | — | 删 |
| `Printer.responseAll` / `useTimes` | — | 删字段 |
| `config/agents.yaml` | - | 已删除,配置收敛到 `AgentSpec` |
| `USE_LEGACY_RUNNER` 环境变量 | — | 删 |
| EventStream(独立类) | — | 合并入 `RuntimeEvent` 的 SSE sink |

---

## 3. 决议记录(已与用户对齐)

| 议题 | 决议 | 备注 |
|---|---|---|
| BaseAgent 何去何从 | **降级为 `AgentSpec` dataclass(无继承)** | 选项 B:保留少量字段方法,无类继承 |
| Legacy/file_clerk 命运 | **直接删 + 重写** | file_clerk 88 行,重写比维护适配器划算 |
| SSE 协议向后兼容 | **直接发 v2,扔掉所有兼容字段** | Web/CLI 均自有,无外部调用方 |
| Agent 注册方式 | **显式 dict,不要装饰器** | 避免导入副作用 |

---

## 4. 分阶段执行计划

每个 Phase 独立可交付、独立可验证。原则:**先删债,再重构,最后补企业能力**。

### Phase 0 — 技术债清点(无代码改动)

**目标:** 把"要删的东西"全部找全,生成精确的删除/修改清单,避免重构中漏删导致循环依赖。

**产出:**
- `docs/REFACTOR_INVENTORY.md`:逐文件列出所有 `Legacy*` / `Pipeline*` / `USE_LEGACY_RUNNER` / `responseAll` / `useTimes` / `legacy_agent_type_map` / `@register_agent` / `BaseAgent` 引用点
- 用 grep 验证清单完整,每个引用都有处置标注:`[删除]` / `[替换]` / `[保留]`

**验证:** 清单评审通过后才进入 Phase 1。

---

### Phase 1 — 删除技术债(破坏性,但范围小)

**改动:**
1. 删除 `handlers/legacy.py` `handlers/pipeline.py` `handlers/base.py`
2. 删除 `agents/adapters/file_clerk_adapter.py` 及其在 services 的引用
3. 删除 `services/agent_orchestration_service.py` 中:
   - `USE_LEGACY_RUNNER` 分支
   - `legacy_agent_type_map` 路由逻辑
   - `legacy_factory` 注入路径
4. 删除 `config/agents.yaml` 整体配置分支
5. 删除 `tests/test_pipeline_legacy_handlers.py`
6. `general_chat` / `deep_research` 暂时仍走 `ReActHandler`(下一阶段处理)

**验证:**
- `pytest` 全绿(剔除 legacy 测试后)
- `run_agent.py` 跑 `general_chat` 和 `deep_research` 行为不变
- `git diff --stat` 应显示净删行数 > 500

**风险:** file_clerk 用户(如果有)会立即失败 — 在 Phase 2 之前不应提供 file_clerk。

---

### Phase 2 — Agent 类降级为 AgentSpec(数据化)

**改动:**
1. 新建 `src/agent_core/spec.py`:
   ```python
   @dataclass(frozen=True)
   class AgentSpec:
       name: str
       system_prompt: str
       model: str = "default"
       max_steps: int = 10
       next_step_prompt: str | None = None
       setup: Callable[[AgentContext], Awaitable[None]] | None = None
   ```
2. `agents/general_chat/` 改造:删 Agent 子类,新建 `spec.py` 输出 `SPEC: AgentSpec = AgentSpec(...)`
3. `agents/deep_research/` 同上
4. `agents/file_clerk/spec.py`:从原 file_clerk 业务逻辑直接重写为 AgentSpec(预计 < 100 行)
5. 新建 `src/agents/__init__.py` 显式 `REGISTRY` dict
6. 删除 `src/agent_core/registry/` 装饰器模块,删除 `src/agent_core/base/agent.py`
7. `services/agent_orchestration_service.py` 改为从 `REGISTRY` 查找 spec

**验证:**
- 三个 agent 全部从 `REGISTRY[name]` 获取
- pytest 全绿
- `grep -r "BaseAgent\|@register_agent" src/` 应该 0 命中

---

### Phase 3 — Loop 函数化(消除 Handler 概念)

**改动:**
1. 新建 `src/agent_core/runtime/turn.py`:
   ```python
   async def run_turn(
       spec: AgentSpec,
       context: AgentContext,
       query: str,
   ) -> AsyncIterator[RuntimeEvent]:
       # 把 ReActHandler._loop 的逻辑移到这里
       # 只 yield RuntimeEvent,不再有 Printer/EventStream 调用
       ...
   ```
2. 删除 `src/agent_core/handlers/` 整个目录
3. `services/agent_orchestration_service.py`:`service.run()` 直接调 `run_turn(spec, ctx, query)`,把事件流分发给注册的 sink

**验证:**
- 整个 `handlers/` 目录消失
- `run_agent.py` 行为不变
- 事件序列与 v0.1 等价(用 golden test 比对 JSONL)

---

### Phase 4 — 事件模型统一(单一 RuntimeEvent)

**改动:**
1. 把 `EventType`(SSE 事件枚举)的所有用例并入 `RuntimeEvent` 子类体系
2. SSE 输出改为 `RuntimeEvent` 的一个 sink:`SseSink.consume(event: RuntimeEvent)`
3. JSONL 日志和 OTel(后续)也是 sink
4. 删除 `src/agent_core/stream/event_stream.py`(独立的 EventStream 类)

**验证:**
- `RuntimeEvent` 的子类是事件的唯一真相源
- 所有 sink 共享同一个事件流,不存在"内部事件 vs 前端事件"的分支

---

### Phase 5 — SSE v2 协议(破坏式升级)

**改动:**
1. `src/agent_core/stream/sse_sink.py` 输出新格式:
   ```
   event: text
   data: {"delta": "..."}

   event: tool_result
   data: {"tool": "...", "ok": true, "result": "...", "elapsed_ms": 42}

   event: done
   data: {"reason": "completed", "usage": {...}}
   ```
2. `run_id` / `conversation_id` 进 SSE comment 或 HTTP header,不每条事件重复
3. 同步改造 `web/src/traceTransport.ts` + `traceReducer.ts`
4. 同步改造 `scripts/chat_pretty.py` + `scripts/renderers/`
5. 更新 `docs/STREAMING_PROTOCOL.md`(写 v2,标注 v1 已废弃)
6. 删除 `Printer` 类的 `responseAll` / `useTimes` / `responseType` 字段

**验证:**
- 终端 CLI 和 Web 控制台均能正常渲染
- 事件 JSON 字节数比 v1 显著减少
- `docs/STREAMING_PROTOCOL.md` 完整描述新协议

---

### Phase 6 — 企业级原语(增量,不破坏)

> Phase 0–5 让框架"瘦"下来后,这一阶段加上企业必备的横切能力。每一项都作为 `run_turn` 的中间件层叠加。

**6.1 多租户上下文**
- `TenantContext(tenant_id, user_id, scopes)` 注入 `AgentContext`
- 所有 `RuntimeEvent` 自动携带 `tenant_id`(通过 contextvar)

**6.2 配额与限流**
- `QuotaMiddleware`:per-tenant token 配额、per-tool 调用频率
- 超限时 emit `QuotaExceededError` 事件

**6.3 审批工作流**
- `ApprovalGate`:工具 `is_destructive=True` 时,事件流暂停,等待外部审批服务回调
- 提供 `approval_decision(run_id, decision)` API

**6.4 OpenTelemetry 追踪**
- 每个 `run_turn` 创建一个 root span
- 每个 LLM 调用、工具调用是子 span
- 通过 OTLP 导出到任意后端(Jaeger / Tempo / Datadog)

**6.5 Secrets 后端**
- 抽象 `SecretsProvider`(Vault / AWS Secrets Manager / 环境变量 fallback)
- LLM API key 通过 provider 取得,不再 `os.getenv`

**6.6 重试与补偿**
- LLM 502/503 → 指数退避重试
- 工具失败 → 按工具自身的 `retry_policy` 决定重试或上抛
- 配置在 `AgentSpec` 或全局 default

**验证:**
- 多租户 e2e 测试:两个租户的事件不串
- 配额 e2e 测试:超限的 run 终止于 `QuotaExceededError`
- OTel 测试:trace 在 collector 中可见、span 关系正确

---

## 5. 风险与回滚

| 风险 | 缓解 |
|---|---|
| Phase 1 删 file_clerk 后业务中断 | 在 Phase 2 完成 file_clerk 重写之前,在内部声明 file_clerk 暂不可用 |
| Phase 5 前端协议变更后 Web 控制台白屏 | Web 与后端**同一个 PR 合并**,前后端原子升级 |
| Phase 3 turn loop 与 v0.1 行为不一致 | 保留 v0.1 的 JSONL 黄金测试,用作 byte-level 比对 |
| 重构中 main 分支不稳定 | 每个 Phase 一个独立 PR;主线随时可发,有问题只回滚最后一个 PR |

**单 Phase 回滚成本:** 都在 1 个 PR 范围内,`git revert` 即可。

---

## 6. 进度追踪

| Phase | 状态 | PR | 完成日期 |
|---|---|---|---|
| 0 — 清点 | ☐ 未开始 | — | — |
| 1 — 删债 | ☐ 未开始 | — | — |
| 2 — AgentSpec 数据化 | ☐ 未开始 | — | — |
| 3 — Loop 函数化 | ☐ 未开始 | — | — |
| 4 — 事件模型统一 | ☐ 未开始 | — | — |
| 5 — SSE v2 | ☐ 未开始 | — | — |
| 6 — 企业级原语 | ☐ 未开始 | — | — |

---

## 7. 设计参考

- **codex**(Rust):
  - `codex-rs/core/src/agent/role.rs` — Role 是声明性配置,无行为
  - `codex-rs/core/src/agent/registry.rs` — 显式 BTreeMap 注册
  - `codex-rs/core/src/tasks/regular.rs` — 唯一的 think→act loop
  - `codex-rs/core/src/agent/mailbox.rs` — 多 agent 间通信(后续可借鉴)

- **claude-code-src**(TypeScript):
  - `query.ts` — 单一 async generator,所有变体走同一个函数
  - `tools/AgentTool/runAgent.ts` — sub-agent 也是同一 `query()` 不同参数
  - `Tool.ts` — Tool 是对象字面量,不是类
