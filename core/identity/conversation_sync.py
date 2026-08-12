"""会话身份同步旧路径兼容导出。

真实实现已迁至 ``core.features.identity.application.conversation_sync``；本模块
只保留单实现 re-export，供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..features.identity.application.conversation_sync import (
    ConversationIdentitySynchronizer,
)

__all__ = ["ConversationIdentitySynchronizer"]
