# concurrency — 并发控制

> `src/agentengine/concurrency/` 提供会话级别的并发控制，防止同一对话的多次运行交错导致记忆丢失。

---

## 模块组成

```
concurrency/
├── lock_manager.py    # ConversationLockManager + InMemoryConversationLockManager
└── __init__.py
```

---

## 问题背景

假设同一个 `conversation_id` 同时收到两个请求：

```
请求 A: load_messages() → run() → save_messages()
请求 B:              load_messages() → run() → save_messages()
```

如果不加控制，B 可能在 A 还没 save 时就 load，导致 B 看不到 A 的更新，最终 A 的修改被 B 覆盖。

---

## ConversationLockManager

**文件：** `src/agentengine/concurrency/lock_manager.py`

### 设计意图

按 `conversation_id` 粒度加锁，保证：
- 同一对话的多次运行**串行执行**
- 不同对话的运行**互不干扰**

```python
class ConversationLockManager(ABC):
    @abstractmethod
    async def acquire(self, conversation_id: str) -> AsyncContextManager:
        ...
```

### 使用

```python
async with self._lock_manager.acquire(context.conversation_id):
    return await runner.run(agent=agent, context=context, query=query, ...)
```

---

## InMemoryConversationLockManager

默认的内存实现：

```python
manager = InMemoryConversationLockManager()
```

- 使用 `asyncio.Lock` 字典
- 适合单进程部署
- 进程重启锁丢失（无持久化需求，因为运行结束就释放）

---

## 分布式锁实现

多进程/多机部署时，需要对接分布式锁：

```python
from agentengine import AgentEngine, RedisConversationLockManager

engine = AgentEngine(
    presets=presets,
    lock_manager=RedisConversationLockManager(redis_client),
)
```

`RedisConversationLockManager` 使用 `SET NX PX` 获取锁，并用 compare-and-delete 脚本释放锁。它不直接依赖 `redis` 包，业务系统传入兼容的 async Redis client 即可。

---

## 关联文档

- [runtime.md](runtime.md) — TurnRunner 在何处获取锁
