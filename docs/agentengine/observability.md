# observability — 可观测性

> `src/agentengine/observability/` 提供结构化运行日志，让每次 Agent 执行都可追溯、可复现、可审计。

---

## 模块组成

```
observability/
├── event_log.py    # RunEventLog — JSONL 日志
├── jsonl_sink.py   # JsonlSink — 写入 JSONL 的 sink
├── otel_sink.py    # OpenTelemetry sink（预留）
└── __init__.py
```

---

## RunEventLog — 运行事件日志

**文件：** `src/agentengine/observability/event_log.py`

### 设计意图

每次 Agent 运行生成一个独立的 JSONL 文件，每行是一个序列化的 `RuntimeEvent`。这比散落的标准输出日志更结构化，便于：
- 事后复盘一次完整的运行过程
- 按 event_type 过滤分析
- 重放运行过程（replay）
- 对接日志分析平台（ELK、Loki 等）

### 存储路径

```
logs/runs/<YYYY-MM-DD>/<run_id>.jsonl
```

例如：`logs/runs/2026-05-09/run_a1b2c3d4e5f6.jsonl`

### 内容示例

```jsonl
{"event_type": "run_started", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "2026-05-09T11:41:44.778938+00:00", "agent_name": "deep_research", "input_summary": "分析架构"}
{"event_type": "turn_started", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "turn": 1}
{"event_type": "text_delta", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "content": "让我"}
{"event_type": "text_delta", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "content": "分析一下"}
{"event_type": "tool_call_started", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "tool_call_id": "call_1", "tool_name": "read_file", "arguments": {"path": "README.md"}}
{"event_type": "tool_call_completed", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "tool_call_id": "call_1", "tool_name": "read_file", "result_summary": "# AgentEngine Refactor...", "elapsed_seconds": 0.04}
{"event_type": "turn_ended", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "turn": 1, "has_tool_calls": true, "elapsed_seconds": 1.23}
{"event_type": "usage_report", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "total_seconds": 2.5}
{"event_type": "run_completed", "run_id": "run_abc", "turn_id": "turn_def", "timestamp": "...", "result_summary": "AgentEngine 是一个声明式 Agent 框架...", "elapsed_seconds": 2.5}
```

### 大小保护

单文件超过 10MB 时自动截断：
- 原文件重命名为 `.jsonl.truncated`
- 后续事件不再写入
- 日志中记录警告

### 读取日志

```python
from agentengine.observability.event_log import RunEventLog

log = RunEventLog("run_abc", base_dir="logs")
records = log.read_records()  # list[dict]

# 过滤特定事件
for r in records:
    if r["event_type"] == "tool_call_failed":
        print(f"Tool failed: {r['tool_name']} - {r['error_message']}")
```

### 环境变量

```bash
export AGENTENGINE_LOG_DIR=logs   # 日志根目录，默认 logs/
```

---

## JsonlSink

**文件：** `src/agentengine/observability/jsonl_sink.py`

`JsonlSink` 实现 `RuntimeEventSink` 协议，被 `RuntimeEventFanout` 调用：

```python
fanout = RuntimeEventFanout([
    JsonlSink(event_log),   # 写入 JSONL
    on_event_callback,       # 传给前端
])
```

---

## 运行事件日志 vs 应用日志

| 维度 | RunEventLog (JSONL) | 应用日志 (logging) |
|------|--------------------|--------------------|
| 格式 | 结构化 JSON，每行一个事件 | 自由文本 |
| 粒度 | 每次运行一个文件 | 全局混合 |
| 用途 | 审计、复盘、重放 | 调试、排障 |
| 位置 | `logs/runs/<日期>/<run_id>.jsonl` | `logs/web-api.log` |
| 消费者 | 分析平台、replay 工具 | 人类开发者 |

---

## 关联文档

- [runtime.md](runtime.md) — TurnRunner 如何创建和使用 RunEventLog
