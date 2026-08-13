"""会话、消息与记忆事件领域模型的旧路径兼容导出。

唯一事实来源已下沉到 ``core.shared.contracts.conversation``；本模块只保留单实现
re-export，供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..shared.contracts.conversation import (
    MemoryEvent,
    Message,
    Session,
    deserialize_from_json,
    serialize_to_json,
)

__all__ = [
    "MemoryEvent",
    "Message",
    "Session",
    "deserialize_from_json",
    "serialize_to_json",
]
