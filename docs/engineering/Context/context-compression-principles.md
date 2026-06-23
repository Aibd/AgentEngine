# 上下文压缩原理

## 为什么需要压缩

LLM 的上下文窗口是有限的定长缓冲区，不是无限的滚动日志。
以 Claude Sonnet 4.6 为例，上限约 100 万 token（≈ 800 万字符）。
一次长对话 + 多轮工具调用的原始 transcript 很容易在数十轮内耗尽这个预算：

```
每轮典型 token 消耗（估算）

  system prompt         ≈    2,000
  用户消息               ≈      500
  模型回复               ≈    1,000
  工具调用参数            ≈      200
  工具返回（文件/搜索结果） ≈   5,000   ← 最大的来源
  ────────────────────────────────
  单轮合计               ≈    8,700
  20 轮后累计            ≈  174,000
  50 轮后累计            ≈  435,000
```

超过窗口上限后 API 返回 `context_length_exceeded` 错误，整个 run 会失败。
压缩的目标是在"接近上限之前"把旧历史折叠成更短的语义等价摘要，
腾出空间给当前和未来的消息。

---

## LLM 上下文的结构

```
┌───────────────────────────────────────────────────────────────┐
│  system: 角色 / 指令 / 工具说明  (固定，不裁剪)                 │
├───────────────────────────────────────────────────────────────┤
│  system: Conversation summary so far: …  (压缩产出，累积叠加)   │
├───────────────────────────────────────────────────────────────┤
│  [受保护消息对]  assistant tool_call + tool result             │
│  (skill activation 对，语义原子，不可拆分)                      │
├───────────────────────────────────────────────────────────────┤
│  user / assistant / tool …  (最近 N 条，保留原文)              │
│  user / assistant / tool …                                    │
│  …                                                            │
├───────────────────────────────────────────────────────────────┤
│  user: 当前用户消息  (当前轮输入)                                │
└───────────────────────────────────────────────────────────────┘
      ▲ 压缩"消化"的是这段：最近 N 条之前的所有非 system 消息
```

---

## AgentEngine 的两条裁剪路径

系统中存在两种独立的减量机制，它们互相补充：

### 路径 A：Memory.\_trim()（无损滑窗，在 append 时触发）

按 `max_messages` 或 `max_tokens` 对历史做**硬截断**：
直接丢弃最旧的若干条消息，不产生任何摘要。

- 优点：零延迟，无 LLM 开销
- 缺点：丢失的信息不可恢复，适合对历史依赖不高的短会话

```python
Memory(max_messages=40, max_tokens=200_000)
```

### 路径 B：LLMSummaryCompactor（有损压缩，在 LLM 调用前触发）

通过 `auto_compact_tokens` 阈值触发，调用同一 LLM 对旧历史生成摘要，
用摘要消息替换原始历史。信息有损但语义保留。

这是本文重点分析的路径。

---

## LLMSummaryCompactor 工作流程

```
输入：agent.memory.snapshot()
         │
         ▼
┌─────────────────────────────────────┐
│ 1. 按 role 分拣                      │
│    system_messages  → 保留原样        │
│    non_system       → 待处理         │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 2. 识别受保护 skill activation 对    │
│    (assistant tool_call + tool_result│
│     metadata["skill_activation"]=True)│
│    → 从 summarizable 中移出，保留原文 │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 3. 切分 recent tail                  │
│    recent   = summarizable[-keep_recent:]  (默认最近 8 条)
│    history  = summarizable[:-keep_recent]  (送摘要)
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 4. 拼接 transcript                   │
│    若存在旧 summary 消息：            │
│      "Previous summaries:\n<旧>\n\n  │
│       Older messages:\n<history>"    │
│    transcript 截取最后 60,000 字符   │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 5. 调用 LLM 生成摘要                  │
│    system: summary_prompt            │
│    user: transcript                  │
│    → summary string                  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 6. 重组 messages                     │
│    system_messages                   │
│    + [system: "Conversation summary  │
│        so far:\n" + summary,         │
│        metadata={"compaction_summary"│
│        : True}]                      │
│    + protected_pairs (展平)           │
│    + recent (去掉 protected 部分)     │
└─────────────────────────────────────┘
```

### 多轮压缩的累积行为

第二次触发压缩时，旧的 summary 消息（`compaction_summary: True`）会被识别，
拼入新 transcript 头部而非被直接摘要。这样历史摘要是**叠加的**，不会被第二次
摘要"再摘要"后导致信息损失加倍。

```
第 1 次压缩后：
  [system: 原始指令]
  [system: summary-v1]   ← compaction_summary=True
  [最近 8 条原文]

第 2 次压缩（再累积了 N 轮）：
  transcript = "Previous summaries:\n<summary-v1>\n\nOlder messages:\n<新的旧历史>"
  → 生成 summary-v2

压缩后：
  [system: 原始指令]
  [system: summary-v2]   ← 整合了 v1 和新旧历史
  [最近 8 条原文]
```

---

## Token 估算的精度问题

目前用的估算函数：

```python
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)
```

这是字符数 / 2 的粗估，对纯 ASCII 代码偏高（实际约 4 字符/token），
对 CJK 文本偏低（实际约 1-2 字符/token）。
误差可达 ±50%，意味着 `auto_compact_tokens` 阈值设置需要留出相当的 buffer。

精确做法是用 tiktoken（OpenAI 族）或 Anthropic 的 `/count_tokens` API，
代价是一次额外网络调用，适合高精度场景。

---

## 与 Claude Code、Codex CLI 的对比

| 维度 | AgentEngine | Claude Code | Codex CLI |
|---|---|---|---|
| 触发时机 | 每轮调用前，超 `auto_compact_tokens` 阈值 | 接近窗口上限自动 | 同左 |
| 默认是否开启 | 否（阈值默认 0） | 是 | 是 |
| 压缩者 | 可注入任意 `Compactor`（默认 LLMSummaryCompactor） | 固定调同一 Claude | 固定调同一模型 |
| 保留原文范围 | 最近 `keep_recent` 条 + skill activation 对 | 当前 user message | 最近 N 条 tool_result |
| 摘要累积 | 多轮叠加（Previous summaries） | 单轮全量替换 | 单轮全量替换 |
| token 计数 | `len//2`（粗估） | tiktoken 精确 | tiktoken 精确 |
| 跨会话持久化 | SQLite（全量 JSON payload） | `~/.claude/` JSONL | 无（in-memory） |

---

## 现有设计的边界与盲区

1. **阈值默认关闭**：`auto_compact_tokens=0` 意味着生产环境中不会触发，
   必须在 `RunConfig` 里显式开启。

2. **估算精度**：字符/2 粗估可能导致触发过早或过晚。

3. **摘要质量不可控**：summary 内容取决于模型，无法验证关键事实是否保留。

4. **工具输出未特殊保护**：大型工具输出（文件读取、搜索结果）会被纳入摘要，
   可能导致具体数值/代码片段失真。只有 skill activation 对受保护。

5. **全量 DB 替换**：每次 `save_to_db` 做 DELETE + INSERT，长对话会有性能问题。
