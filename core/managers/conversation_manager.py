"""
会话管理器 - ConversationManager
提供高级的会话和消息管理功能

功能:
- 会话生命周期管理
- LRU缓存热点会话
- 上下文窗口管理
- 群聊场景支持
- AstrBot事件集成
"""

import asyncio
from collections import OrderedDict
from typing import Any

from astrbot.api import logger

from ..identity.runtime import ProtocolIdentityRuntime
from ..storage.conversation_store import ConversationStore
from .event_adapter import EventAdapterMixin
from .message_operations import MessageOperationsMixin
from .range_and_metadata import RangeAndMetadataMixin
from .session_cache import SessionCacheMixin
from .session_lifecycle import SessionLifecycleMixin


class ConversationManager(
    EventAdapterMixin,
    MessageOperationsMixin,
    SessionLifecycleMixin,
    RangeAndMetadataMixin,
    SessionCacheMixin,
):
    """
    会话管理器 - 提供高级的会话和消息管理功能

    功能:
    - 会话生命周期管理
    - LRU缓存热点会话
    - 上下文窗口管理
    - 群聊场景支持
    - AstrBot事件集成
    """

    def __init__(
        self,
        store: ConversationStore,
        max_cache_size: int = 100,
        context_window_size: int = 50,
        session_ttl: int = 3600,
        identity_runtime: ProtocolIdentityRuntime | None = None,
    ) -> None:
        """
        初始化会话管理器

        Args:
            store: ConversationStore实例
            max_cache_size: LRU缓存大小
            context_window_size: 上下文窗口大小(保留最近N条消息)
            session_ttl: 会话过期时间(秒)
            identity_runtime: 可选的协议身份运行时
        """
        self.store = store
        self.max_cache_size = max_cache_size
        self.context_window_size = context_window_size
        self.session_ttl = session_ttl
        self.identity_runtime = identity_runtime or ProtocolIdentityRuntime()

        # LRU缓存: {session_id: (messages, last_access_time)}
        self._cache: OrderedDict = OrderedDict()
        # 缓存锁，保护并发访问
        self._cache_lock = asyncio.Lock()

        logger.info(
            f"[ConversationManager] 初始化完成: "
            f"缓存大小={max_cache_size}, 上下文窗口={context_window_size}"
        )

    async def close_identity_runtime(self) -> None:
        """关闭身份运行时持有的独立 Store 连接。"""

        await self.identity_runtime.close()


def create_conversation_manager(
    db_path: str, config: dict[str, Any] | None = None
) -> ConversationManager:
    """
    便捷创建函数

    Args:
        db_path: 数据库路径
        config: 配置字典,可包含:
            - max_cache_size: LRU缓存大小
            - context_window_size: 上下文窗口大小
            - session_ttl: 会话过期时间

    Returns:
        ConversationManager实例
    """
    config = config or {}
    store = ConversationStore(db_path)

    return ConversationManager(
        store=store,
        max_cache_size=config.get("max_cache_size", 100),
        context_window_size=config.get("context_window_size", 50),
        session_ttl=config.get("session_ttl", 3600),
    )
