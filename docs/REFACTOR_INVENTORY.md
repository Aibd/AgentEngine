# Phase 0 技术债清单 — 完整引用点

> 状态:**Phase 0 产出,等待用户审核后进入 Phase 1**
> 制定日期:2026-05-07
> 配套计划:[REFACTOR_PLAN.md](REFACTOR_PLAN.md)

本清单逐文件列出所有要在 Phase 1–5 中删除/替换/保留的代码位置,确保重构不漏点不留死代码。

**处置标记:**
- 🔴 **[删]** — 整个文件或行段直接删除
- 🟠 **[替]** — 替换为新实现(行号指当前位置)
- 🟢 **[保]** — 保留(暂不动)
- 📝 **[文档]** — 文档同步,不影响代码

---

## A. `USE_LEGACY_RUNNER` 后门(Phase 1 删)

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/services/agent_orchestration_service.py:198-199` | `_use_legacy_runner()` 方法 | 🔴 删 |
| `src/services/agent_orchestration_service.py` | 调用 `_use_legacy_runner()` 的所有分支 | 🔴 删(grep 后清理) |
| `tests/test_full_run_e2e.py:12` | `monkeypatch.delenv("USE_LEGACY_RUNNER", ...)` | 🔴 删该行 |
| `quick_start.cmd:23` | `echo USE_LEGACY_RUNNER=false>> .env` | 🔴 删该行 |
| `README.md:54-59` | 整段说明 + 切换示例 | 📝 删段 |
| `RUNNING_GUIDE.md:19, 131, 225` | 三处提及 | 📝 删 |
| `PROJECT_TOUR.md:386, 522` | 两处提及 | 📝 删 |

---

## B. `LegacyHandler` + `legacy_factory` + `legacy_agent_type_map`(Phase 1 删)

### B.1 代码

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/handlers/legacy.py`(整文件 88 行) | LegacyHandler 类 | 🔴 删整文件 |
| `src/agentengine/handlers/__init__.py:2, 6` | `from ... import LegacyHandler` 导出 | 🔴 删 |
| `src/agents/adapters/file_clerk_adapter.py`(整文件 88 行) | FileClerkAdapter | 🔴 删整文件 |
| `src/agents/adapters/__init__.py`(整文件) | 导出 FileClerkAdapter | 🔴 删整文件 |
| `src/agents/adapters/`(整目录) | 适配器目录 | 🔴 删整目录 |
| `config/agents.yaml` | Agent YAML 配置 | ✅ 已删除,改用 `agents.REGISTRY` + `AgentSpec` |

### B.2 测试

| 位置 | 内容 | 处置 |
|---|---|---|
| `tests/test_pipeline_legacy_handlers.py`(整文件) | LegacyHandler/PipelineHandler 测试 | 🔴 删整文件 |
| `tests/test_core.py:31-47` | `test_file_clerk_adapter_runs_legacy_factory` | 🔴 删该测试函数 |
| `tests/test_lifecycle.py:14, 133-154` | `from ...file_clerk_adapter import FileClerkAdapter` 与 `test_file_clerk_cancellation_cancels_legacy_task` | 🔴 删该 import 与该测试 |

### B.3 文档

| 位置 | 内容 | 处置 |
|---|---|---|
| `README.md:3, 11, 34, 78-80` | LegacyHandler / FileClerkAdapter 描述 | 📝 改写 |
| `docs/README.md:20, 64, 183-187` | 同上 | 📝 改写 |
| `docs/STREAMING_PROTOCOL.md:238` | "LegacyHandler 包装的业务代码" | 📝 改写 |
| `PROJECT_TOUR.md:17, 43, 146, 364-367, 385, 416-423, 485, 522` | 多处提及 | 📝 改写(本次 Phase 1 仅删代码,文档改造可放 Phase 2) |
| `RUNNING_GUIDE.md:113-116` | yaml 示例 | 📝 改写 |

---

## C. `PipelineHandler`(Phase 1 删)

### C.1 代码

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/handlers/pipeline.py`(整文件 88 行) | PipelineHandler 类 | 🔴 删整文件 |
| `src/agentengine/handlers/__init__.py:3, 6` | 导出 | 🔴 删 |
| `src/agentengine/base/agent.py:13, 61` | docstring + `pipeline_steps()` 方法 | 🟠 在 Phase 2 整体替换 BaseAgent 时一并删 |

### C.2 测试

| 位置 | 内容 | 处置 |
|---|---|---|
| `tests/test_lifecycle.py:11, 66, 127` | PipelineHandler import + 两处使用 | 🔴 删 import 与相关测试用例 |

### C.3 文档

| 位置 | 内容 | 处置 |
|---|---|---|
| `docs/API.md:13` | `pipeline_steps()` 接口 | 📝 删 |
| `docs/README.md:19, 173` | 描述 | 📝 改写 |
| `README.md:11, 34` | 描述 | 📝 改写 |
| `PROJECT_TOUR.md:43, 145, 419` | 描述 | 📝 改写 |

---

## D. `BaseAgent` 继承体系(Phase 2 替换为 `AgentSpec`)

### D.1 框架代码(替换为 AgentSpec)

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/base/agent.py`(整文件) | `class BaseAgent` | 🟠 删,新建 `src/agentengine/spec.py` 定义 `AgentSpec` |
| `src/agentengine/base/__init__.py:1, 5` | 导出 BaseAgent | 🟠 改为导出 AgentSpec(或整个 base/ 重命名) |
| `src/agentengine/__init__.py:1, 5` | 顶层导出 | 🟠 改为 AgentSpec |
| `src/agentengine/runtime/turn_runner.py:10, 48` | `agent: BaseAgent` 形参 | 🟠 Phase 3 整体替换为 `spec: AgentSpec` |

### D.2 业务 Agent 子类(全部改为 `SPEC: AgentSpec` 数据)

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agents/general_chat/agent.py`(整文件) | `class GeneralChatAgent(BaseAgent)` | 🟠 改为 `spec.py` 输出 `SPEC: AgentSpec` |
| `src/agents/deep_research/agent.py`(整文件) | `class DeepResearchAgent(BaseAgent)` | 🟠 同上 |
| `src/agents/__init__.py`(整文件) | 装饰器副作用导入注释 | 🟠 改为显式 `REGISTRY: dict[str, AgentSpec]` |

### D.3 ReActHandler 中的 BaseAgent 引用(Phase 3 重构 turn loop 时一并处理)

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/handlers/react.py:9, 31, 46, 119, 141, 308, 324` | 7 处 `agent: BaseAgent` | 🟠 Phase 3 turn loop 函数化时全部转为 `spec: AgentSpec` |

### D.4 测试

| 位置 | 内容 | 处置 |
|---|---|---|
| `tests/test_lifecycle.py:8, 37, 64, 79` | 4 处 BaseAgent 引用 + `_TeardownAgent` 子类 | 🟠 Phase 2/3 改写为 AgentSpec |
| `tests/test_observability.py:7, 31` | `class _ToolAgent(BaseAgent)` | 🟠 改写 |
| `tests/test_react_handler.py:5, 27, 34` | 两个测试 Agent 子类 | 🟠 改写 |
| `tests/test_turn_runner.py:7, 17, 25, 31, 73, 96` | 6 处 BaseAgent 用法 | 🟠 改写 |
| `tests/test_registry.py`(整文件) | 装饰器注册测试 | 🔴 删整文件(Phase 2,装饰器机制不保留) |

---

## E. `@register_agent` / `@register_handler` 装饰器(Phase 2 删)

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/registry/`(整目录,2 文件) | agent_registry.py + handler_registry.py | 🔴 删整目录 |
| `src/agents/general_chat/agent.py:5` | `@register_agent("general_chat", handler="react")` | 🔴 删(随类一起重写) |
| `src/agents/deep_research/agent.py:7` | 同上 | 🔴 删 |
| `src/agents/adapters/file_clerk_adapter.py:26` | 同上(已在 B 中删) | — |
| `src/agentengine/handlers/react.py:16, 26` | `@register_handler("react")` | 🔴 删(Phase 3 函数化时整体删) |
| `src/agentengine/handlers/legacy.py:10, 16` | 同上(已在 B 中删) | — |
| `src/agentengine/handlers/pipeline.py:11, 18` | 同上(已在 C 中删) | — |
| `tests/test_registry.py`(整文件) | 装饰器测试 | 🔴 删 |
| `docs/API.md:122-123` | API 文档 | 📝 删 |
| `docs/README.md:36-37, 80-95, 377-393, 527-564` | 多处教程示例 | 📝 改写 |

---

## F. SSE 信封兼容字段:`responseAll` / `useTimes` / `responseType`(Phase 5 删)

> 注意:`responseType` 字段本身要换成 SSE 原生 `event:` 行,不再放在 JSON body 里。Phase 5 是破坏式协议升级。

### F.1 框架代码

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/stream/printer.py:31, 61, 63, 64` | 信封定义中的三个字段 | 🔴 删字段 |
| `src/agentengine/stream/printer.py`(整文件) | Printer 大量基于 `EventType.value` 的分支 | 🟠 Phase 4/5 整体重写为 RuntimeEvent → SSE sink |
| `src/agentengine/stream/events.py`(整文件) | `EventType` 枚举 | 🟠 Phase 4 并入 RuntimeEvent 体系 |
| `src/agentengine/stream/event_stream.py`(整文件) | `EventStream` 类 | 🟠 Phase 4 改为 SSE sink consumer |
| `src/agentengine/stream/__init__.py:1` | 导出 EventStream | 🟠 改 |
| `scripts/_smoke_pretty.py:41-44` | 测试脚本写死字段 | 🔴 改写为 v2 信封 |

### F.2 前端

| 位置 | 内容 | 处置 |
|---|---|---|
| `web/src/types.ts:19-22` | `responseType` `responseAll` `useTimes` 类型 | 🟠 v2 改为 `event: string; data: unknown` |
| `web/src/traceTransport.ts:80-83` | 错误事件构造 | 🟠 改 |
| `web/src/traceReducer.ts:14` | `switch (event.responseType)` | 🟠 改为 `switch (event.event)` |
| `web/src/App.tsx:82` | `event.responseType === "error"` | 🟠 改 |
| `scripts/renderers/rich_renderer.py:77` | `event.get("responseType")` | 🟠 改 |

### F.3 测试 fixtures(全部废弃,重写)

| 位置 | 内容 | 处置 |
|---|---|---|
| `tests/fixtures/sse_golden/*.jsonl`(共 11 个文件) | v1 黄金事件 | 🔴 删整目录 |
| `tests/test_sse_golden_compatibility.py` | 黄金兼容测试 | 🔴 删整文件 |
| `tests/test_streaming_protocol.py:78-347` | 大量 `e["responseType"]` 断言 | 🟠 全部改写为 v2 |
| `tests/test_stream.py:49-123` | 同上 | 🟠 改写 |
| `tests/test_full_run_e2e.py:41` | 同上 | 🟠 改 |
| `tests/test_react_handler.py:95, 159` | 同上 | 🟠 改 |
| `tests/test_printer_runtime_event_mapping.py:71` | 同上 | 🟠 改 |
| `tests/test_rich_renderer.py:38-41` | 测试构造 v1 信封 | 🟠 改 |
| `tests/test_web_api.py:88, 97` | 同上 | 🟠 改 |

### F.4 文档

| 位置 | 内容 | 处置 |
|---|---|---|
| `docs/STREAMING_PROTOCOL.md`(整文件) | v1 协议规范 | 📝 改写为 v2 + 标注 v1 已废弃 |
| `docs/API.md:70-82` | v1 字段说明 | 📝 改写 |
| `docs/README.md:475-478` | v1 信封示例 | 📝 改写 |
| `README.md:46` | v1 字段列表 | 📝 改写 |
| `PROJECT_TOUR.md:58, 290-305, 600` | v1 字段说明 | 📝 改写 |

---

## G. 双轨事件:`EventStream` vs `RuntimeEvent`(Phase 4 统一)

### G.1 框架代码(把 EventStream 降级为 RuntimeEvent 的 sink)

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/stream/event_stream.py`(整文件) | EventStream 独立类 | 🟠 改为 SSE sink |
| `src/agentengine/runtime/events.py:32` | docstring 强调"两层分离" | 📝 改 docstring(Phase 4 后 RuntimeEvent 是唯一真相源) |
| `src/services/agent_orchestration_service.py:18, 244, 245` | EventStream 创建/返回 | 🟠 Phase 4 改为返回 RuntimeEvent 流 |
| `tests/conftest.py:8, 24, 25, 29` | EventStream fixture | 🟠 Phase 4 改 |

### G.2 测试

| 位置 | 内容 | 处置 |
|---|---|---|
| `tests/test_stream.py`(整文件) | EventStream/Printer 测试(11 处) | 🟠 Phase 4 改 |
| `tests/test_react_handler.py:12, 55-72` | EventStream 引用 | 🟠 改 |
| `tests/test_printer_runtime_event_mapping.py:14, 65` | 同上 | 🟠 改 |
| `scripts/chat.py:21, 76, 113, 123` | CLI 用 EventStream | 🟠 改 |

### G.3 文档

| 位置 | 内容 | 处置 |
|---|---|---|
| `README.md:16, 52` | EventStream/EventType 描述 | 📝 改 |
| `docs/STREAMING_PROTOCOL.md:250, 267-280` | 双层架构图 | 📝 改 |
| `docs/README.md:43-44, 110, 433-447` | 描述 | 📝 改 |
| `PROJECT_TOUR.md:57, 124, 128, 335, 444, 474, 521, 536` | 多处 | 📝 改 |
| `src/README.md:14` | 同上 | 📝 改 |

---

## H. 其他散点

| 位置 | 内容 | 处置 |
|---|---|---|
| `src/agentengine/handlers/base.py`(整文件,12 行) | `AgentHandler` 协议 | 🔴 Phase 3 删整文件(loop 函数化后无需协议) |
| `src/agentengine/handlers/__init__.py`(整文件) | handlers 包导出 | 🔴 Phase 3 删整目录 |
| `src/agentengine/handlers/`(整目录) | 整个 handlers 模块 | 🔴 Phase 3 删 |

---

## 删除统计预估

| 类别 | 文件数 | 行数(估算) |
|---|---|---|
| `handlers/legacy.py` + `pipeline.py` + `base.py` | 3 | 188 |
| `handlers/react.py` | 1 | 425(Phase 3 重写为 turn.py,行数减半) |
| `handlers/__init__.py` | 1 | ~10 |
| `agents/adapters/` | 2 | ~95 |
| `registry/` | 3 | ~50 |
| `base/agent.py` | 1 | ~70 |
| `tests/test_pipeline_legacy_handlers.py` | 1 | ~70 |
| `tests/test_registry.py` | 1 | ~50 |
| `tests/test_sse_golden_compatibility.py` | 1 | ~100 |
| `tests/fixtures/sse_golden/` | 11 | ~11 |
| **小计(净删)** | **25** | **~1100** |

> Phase 3 后 `react.py:425` 重写为 `turn.py`(预计 200-250 行),净减 175-225 行。
> Phase 1 完成后 `git diff --stat` 应显示净删行数 > 500。

---

## 不在删除清单中的(刻意保留)

这些虽然名字相关,但属于核心能力,**不删**:

| 位置 | 为什么保留 |
|---|---|
| `src/agentengine/runtime/turn_runner.py` | Phase 3 改为更薄,但保留 run/turn 框架 |
| `src/agentengine/runtime/events.py`(RuntimeEvent 子类) | Phase 4 后这是唯一真相源 |
| `src/agentengine/runtime/run_state.py` | run 生命周期状态机,保留 |
| `src/agentengine/handlers/react.py` 的核心 think→act 逻辑 | 内容迁移到 `runtime/turn.py`,不丢失 |
| `src/agentengine/stream/printer.py` 的 SSE 序列化能力 | Phase 5 重写为 v2,不删整文件 |
| `tests/test_react_handler.py` 的 LLM/工具 mock 测试 | 改写到 turn loop 后保留 |

---

## Phase 0 完成检查表

- [x] grep `USE_LEGACY_RUNNER` — 完整
- [x] grep `LegacyHandler` / `legacy_factory` / `legacy_agent_type_map` / `legacy_adapter` — 完整
- [x] grep `PipelineHandler` — 完整
- [x] grep `BaseAgent` / `@register_agent` / `register_handler` — 完整
- [x] grep `file_clerk` / `FileClerk` — 完整
- [x] grep `responseAll` / `useTimes` / `responseType` — 完整
- [x] grep `EventStream` / `EventType` — 完整
- [x] 删除 `config/agents.yaml` 配置分支 — 完整
- [x] 验证 `services/agent_orchestration_service.py` 含 USE_LEGACY_RUNNER — 完整
- [x] 列出 `registry/` `stream/` `runtime/` 目录结构 — 完整

**Phase 0 已完成,清单待用户审核后进入 Phase 1。**
