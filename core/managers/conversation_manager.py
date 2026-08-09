"""会话、消息缓存与 AstrBot 事件适配管理器。"""

import asyncio
from collections import OrderedDict
from typing import Any

from astrbot.api import logger

from ..shared.contracts import IdentityConversationPort
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
    """管理会话生命周期、缓存、历史消息及其身份端口引用。"""

    def __init__(
        self,
        store: ConversationStore,
        max_cache_size: int = 100,
        context_window_size: int = 50,
        session_ttl: int = 3600,
        identity_runtime: IdentityConversationPort | None = None,
    ) -> None:
        """初始化会话管理器。

        参数:
            store: 已初始化的会话持久化 Store。
            max_cache_size: LRU 缓存中最多保留的会话数量。
            context_window_size: 单会话上下文最多保留的消息数量。
            session_ttl: 会话缓存的过期秒数。
            identity_runtime: 由组合根注入的身份会话端口；缺失时保持关闭。
        """
        self.store = store
        self.max_cache_size = max_cache_size
        self.context_window_size = context_window_size
        self.session_ttl = session_ttl
        self.identity_runtime = identity_runtime

        # LRU缓存: {session_id: (messages, last_access_time)}
        self._cache: OrderedDict = OrderedDict()
        # 缓存锁，保护并发访问
        self._cache_lock = asyncio.Lock()

        logger.info(
            f"[ConversationManager] 初始化完成: "
            f"缓存大小={max_cache_size}, 上下文窗口={context_window_size}"
        )

    async def close_identity_runtime(self) -> None:
        """兼容旧关闭入口；仅委托显式提供关闭能力的已注入运行时。"""

        close = getattr(self.identity_runtime, "close", None)
        if callable(close):
            await close()


def create_conversation_manager(
    db_path: str, config: dict[str, Any] | None = None
) -> ConversationManager:
    """按配置创建会话管理器，身份端口留待组合根后续注入。

    参数:
        db_path: 会话 SQLite 数据库路径。
        config: 可选配置，可含缓存大小、上下文窗口和会话过期秒数。

    返回:
        未拥有身份运行时的会话管理器实例。
    """
    config = config or {}
    store = ConversationStore(db_path)

    return ConversationManager(
        store=store,
        max_cache_size=config.get("max_cache_size", 100),
        context_window_size=config.get("context_window_size", 50),
        session_ttl=config.get("session_ttl", 3600),
    )
