"""会话管理器工厂，按配置组合 Store 与 Manager。"""

from typing import Any

from ..application.conversation_manager import ConversationManager
from .conversation_store import ConversationStore


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


__all__ = ["create_conversation_manager"]
