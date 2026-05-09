# persistence — 持久化抽象

> `src/agentengine/persistence/` 提供对话历史和运行结果的持久化抽象，框架不依赖具体数据库，由业务层注入实现。

---

## 模块组成

```
persistence/
├── port.py       # PersistencePort Protocol
├── sqlite.py     # SQLite 实现（示例/测试用）
└── __init__.py
```

---

## PersistencePort — 抽象接口

**文件：** `src/agentengine/persistence/port.py`

### 设计意图

AgentEngine 是框架代码，不依赖任何 ORM 或数据库。`PersistencePort` 定义了框架需要的持久化操作，业务层（如 `gjsk_wiseagent_ai`）注入自己的实现（SQLAlchemy、Django ORM 等）。

```python
@runtime_checkable
class PersistencePort(Protocol):
    async def save_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        agent_name: str,
        input_msg: str,
        reply_msg: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def save_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None: ...

    async def load_messages(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]: ...

    async def save_artifact(
        self,
        run_id: str,
        artifact_type: str,
        data: dict[str, Any],
    ) -> None: ...
```

### 方法说明

| 方法 | 用途 | 调用时机 |
|------|------|---------|
| `save_run` | 保存一次运行的输入和最终回复 | TurnRunner 结束后（可选） |
| `save_messages` | 保存对话消息列表 | run_turn 结束时（自动） |
| `load_messages` | 加载历史对话消息 | run_turn 开始时（自动） |
| `save_artifact` | 保存工具生成的产物 | 业务层按需调用 |

---

## 注入方式

```python
from services.agent_orchestration_service import AgentOrchestrationService

# 方式 1：注入 Service
service = AgentOrchestrationService(persistence=my_persistence_impl)

# 方式 2：注入 Context
context.persistence = my_persistence_impl
```

---

## 使用流程

```
AgentOrchestrationService.run()
    │
    ├──► context.persistence = service._persistence
    │
    ├──► TurnRunner.run()
    │       │
    │       ├──► run_turn()
    │       │       │
    │       │       ├──► memory.load_from_db(persistence, conversation_id)
    │       │       │       └──► persistence.load_messages("conv-123")
    │       │       │
    │       │       ├──► 执行循环...
    │       │       │
    │       │       └──► memory.save_to_db(persistence, conversation_id)
    │       │               └──► persistence.save_messages("conv-123", [...])
    │       │
    │       └──► return
    │
    └──► service._record_agent_finish()  # 可选：保存运行记录
```

---

## SQLite 示例实现

**文件：** `src/agentengine/persistence/sqlite.py`

项目内置了一个 SQLite 实现，用于本地开发和测试：

```python
from agentengine.persistence.sqlite import SqlitePersistence

persistence = SqlitePersistence(db_path="data/chatbot.db")
```

表结构：
- `conversations` — 会话元数据
- `messages` — 消息记录（OpenAI 格式 JSON）
- `runs` — 运行记录
- `artifacts` — 工具产物

---

## 实现自定义 Persistence

```python
from agentengine.persistence.port import PersistencePort

class MyPersistence(PersistencePort):
    async def save_messages(self, conversation_id: str, messages: list[dict]):
        await my_db.messages.insert_many([
            {"conversation_id": conversation_id, "content": json.dumps(m)}
            for m in messages
        ])

    async def load_messages(self, conversation_id: str):
        rows = await my_db.messages.find({"conversation_id": conversation_id})
        return [json.loads(r["content"]) for r in rows]

    # ... 其他方法
```

---

## 关联文档

- [memory.md](memory.md) — Memory 如何调用 PersistencePort
- [base.md](base.md) — AgentContext 的 persistence 字段
