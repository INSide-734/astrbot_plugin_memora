"""会话领域模型的 feature 内兼容导出。

唯一事实来源已下沉到 ``core.shared.contracts.conversation``；本模块保留
re-export 以兼容 conversation feature 内的历史调用方。
"""

from ....shared.contracts.conversation import (
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
