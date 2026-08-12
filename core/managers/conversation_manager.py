"""会话管理器旧路径兼容导出。

真实实现已迁至 ``core.features.conversation``；本模块只保留单实现 re-export，
供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..features.conversation.application.conversation_manager import (
    ConversationManager,
)
from ..features.conversation.infrastructure.conversation_manager_factory import (
    create_conversation_manager,
)

__all__ = ["ConversationManager", "create_conversation_manager"]
