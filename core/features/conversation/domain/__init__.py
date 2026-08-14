"""会话 feature 的领域配置与数据模型。"""

from ....shared.contracts.conversation import (
    MemoryEvent,
    Message,
    Session,
    deserialize_from_json,
    serialize_to_json,
)
from .config import SessionManagerConfig

__all__ = [
    "MemoryEvent",
    "Message",
    "Session",
    "SessionManagerConfig",
    "deserialize_from_json",
    "serialize_to_json",
]
