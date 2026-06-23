# 上下文压缩改造方案

基于[原理分析](context-compression-principles.md)，本文给出可落地的改造路线。
改动按优先级分为三档，每档独立可交付。

---

## 优先级 P0：让压缩在生产中实际生效

### 现状

`auto_compact_tokens` 默认为 `0`，整个压缩机制在生产中不触发。

### 改动

在 `AgentDefinition` 默认值或 `AGENT_REGISTRY` 注册处开启阈值：

```python
# app/backend/agents/__init__.py 或各 agent 定义文件
AgentDefinition(
    name="deep_research",
    instructions="...",
    auto_compact_tokens=800_000,    # Sonnet 4.6 窗口 1M，留 20% buffer
    compaction_keep_recent=12,      # 保留最近 12 条（默认 8 偏少）
)
```

同时在 Web API 请求入口（`web_api.py`）为 AgentOrchestrationService 设置全局兜底：

```python
# 若 agent 定义未设置阈值，在 RunConfig 层兜底
DEFAULT_COMPACT_TOKENS = 800_000
```

**为什么是 800,000？**
Sonnet 4.6 上限 1,000,000 token。留 20% 作为：
- 当前轮模型输出空间
- token 估算误差（粗估可偏差 ±50%）
- system prompt 和工具 schema 占用

---

## 优先级 P1：修正 token 估算精度

### 现状

```python
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)  # 字符/2，误差 ±50%
```

### 改动方案 A：分字符集估算（零依赖，误差降至 ±20%）

```python
import unicodedata

def _estimate_tokens(text: str) -> int:
    cjk = sum(
        1 for ch in text
        if unicodedata.east_asian_width(ch) in ("W", "F")
    )
    ascii_like = len(text) - cjk
    # CJK: ~1.5 char/token，ASCII: ~4 char/token
    return max(1, cjk // 1 + ascii_like // 4)
```

### 改动方案 B：调用 Anthropic count_tokens API（精确，有延迟）

```python
# src/agentengine/memory/memory.py
async def estimated_tokens_exact(self, llm: LLMClient) -> int:
    """调用 /count_tokens 精确计数，适合阈值临界判断。"""
    payload = [m.to_openai() for m in self.snapshot()]
    return await llm.count_tokens(payload)
```

在 `_maybe_compact` 里，仅当粗估超过阈值 80% 时才调精确计数。这样
绝大多数轮次走快路径，只在真正临界时多一次 API 调用：

```python
async def _maybe_compact(agent, context):
    rough = agent.memory.estimated_tokens()
    threshold = agent.config.auto_compact_tokens
    if threshold <= 0 or rough < threshold * 0.8:
        return                          # 明确不需要压缩
    if rough < threshold:
        # 临界区：精确计数确认
        exact = await estimated_tokens_exact(agent.memory, context.llm)
        if exact < threshold:
            return
    await _do_compact(agent, context)
```

**推荐 A 方案先行**，不引入新的网络依赖，精度足够驱动 P0 的阈值配置。

---

## 优先级 P1：保护大型工具输出原文

### 现状

工具输出（文件读取、搜索结果、代码执行输出）会被纳入摘要，
具体数值和代码片段有失真风险。只有 `skill_activation` 对受保护。

### 改动：引入 `preserve_in_compaction` metadata 标记

在工具执行结果写入 memory 时，对超过一定体量的工具输出打标：

```python
# src/agentengine/runtime/turn.py，_execute_tool_calls 内
tool_messages.append(
    Message.tool(
        result.content,
        tool_call_id=tc.get("id", ""),
        metadata={
            **(result.metadata or {}),
            # 超过 2000 字符的工具输出标记为需保留原文
            "preserve_in_compaction": len(result.content) > 2_000,
        },
    )
)
```

在 `LLMSummaryCompactor._extract_protected_pairs` 扩展保护逻辑：

```python
@staticmethod
def _extract_protected_tool_outputs(messages):
    """返回需要保留原文的大型工具输出消息（连同对应的 assistant 调用）。"""
    protected = []
    for idx, msg in enumerate(messages):
        if msg.role is not Role.TOOL:
            continue
        if not msg.metadata.get("preserve_in_compaction"):
            continue
        # 找对应 assistant 调用
        tool_call_id = msg.tool_call_id
        for prev in reversed(messages[:idx]):
            if prev.role is Role.ASSISTANT and prev.tool_calls:
                if any(c.get("id") == tool_call_id for c in prev.tool_calls):
                    protected.append((prev, msg))
                    break
    return protected
```

只保护**最近 3 个**大型工具输出，防止保护集本身把窗口撑满。

---

## 优先级 P2：摘要质量验证

### 现状

压缩后无法验证关键事实是否保留，摘要质量完全取决于模型。

### 改动：压缩后 diff 检查（可选，适合高价值对话）

```python
@dataclass
class VerifyingCompactor:
    """包装另一个 Compactor，压缩后验证关键实体是否保留。"""
    inner: Compactor
    llm: LLMClient

    async def compact(self, messages):
        compacted = await self.inner.compact(messages)

        # 让模型从原始历史提取关键实体列表
        entities = await self._extract_entities(messages)
        # 检查摘要中是否覆盖这些实体
        missing = await self._check_coverage(compacted, entities)

        if missing:
            # 把遗漏的实体补充到 summary 消息内容尾部
            summary_msg = next(
                (m for m in compacted if m.metadata.get("compaction_summary")),
                None,
            )
            if summary_msg:
                summary_msg.content += f"\n\n[补充关键信息]\n" + "\n".join(missing)

        return compacted
```

这是可选增强，每次压缩多 2 次 LLM 调用，适合 deep_research 这类高精度场景。

---

## 优先级 P2：持久化优化（增量追加替代全量替换）

### 现状

每次 `save_to_db` 做 `DELETE + INSERT`，100 条消息 × 每轮保存 = O(n) 开销递增。

### 改动：水位线增量追加

```python
# SqlitePersistence 增加 watermark 记录
# messages 表增加列：synced BOOLEAN DEFAULT 0

async def save_messages_incremental(self, conversation_id, messages):
    """只写入上次保存以来新增的消息。"""
    await asyncio.to_thread(
        self._save_incremental_sync, conversation_id, messages
    )

def _save_incremental_sync(self, conversation_id, messages):
    with self._connect() as conn:
        cursor = conn.execute(
            "SELECT MAX(position) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        last_pos = cursor.fetchone()[0] or -1
        new_messages = [
            (m, i) for i, m in enumerate(messages) if i > last_pos
        ]
        if not new_messages:
            return
        # 仅 INSERT 新增消息
        conn.executemany(
            "INSERT INTO messages (...) VALUES (...)",
            [make_row(m, i, conversation_id) for m, i in new_messages],
        )
```

**注意**：压缩后必须用全量替换（旧消息 position 失效），所以需要记录
"本次是否做了压缩"，压缩轮次走现有全量替换，普通轮次走增量追加。

---

## 改动优先级汇总

| 优先级 | 改动 | 文件 | 复杂度 | 收益 |
|---|---|---|---|---|
| P0 | 开启默认阈值 | `agents/`, `run_config.py` | 低 | 让压缩实际生效 |
| P1 | 分字符集 token 估算 | `memory/memory.py` | 低 | 估算误差 ±20% |
| P1 | 保护大型工具输出 | `runtime/turn.py`, `compaction.py` | 中 | 防止代码/数值摘要失真 |
| P2 | 摘要质量验证 | `compaction.py` | 高 | high-value agent 精度 |
| P2 | 增量持久化 | `persistence/sqlite.py` | 中 | 长对话写入 O(1) |

**建议执行顺序**：P0 → P1（估算）→ P1（工具保护）→ P2（按需）。
P0 和 P1-估算 可以在同一 PR 内合并交付。

---

## 附：配置速查

```python
# RunConfig 完整压缩相关参数
RunConfig(
    auto_compact_tokens=800_000,     # 触发阈值，0=关闭
    compaction_keep_recent=12,       # 保留最近 N 条原文
    compactor=LLMSummaryCompactor(   # 默认，可替换
        llm=my_llm,
        keep_recent=12,
        max_input_chars=80_000,      # 摘要输入字符上限（默认 60,000）
        summary_prompt="...",        # 自定义摘要 prompt
    ),
)

# Memory 独立裁剪（与压缩互相独立）
Memory(
    max_messages=60,      # 超过 60 条硬裁，0=不限
    max_tokens=400_000,   # token 预算硬裁，0=不限
)
```
