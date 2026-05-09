"""Concurrency primitives — conversation-scoped serialization.

A chatbot built on this framework must guarantee that two requests for the
same `conversation_id` cannot interleave their memory load/save cycles, or
the second request silently loses the first's turn. The default backend is
an in-process asyncio.Lock dictionary; multi-replica deployments should
swap in a Redis-based implementation behind the same interface.
"""

from agentengine.concurrency.lock_manager import (
    ConversationLockManager,
    InMemoryConversationLockManager,
)

__all__ = [
    "ConversationLockManager",
    "InMemoryConversationLockManager",
]
