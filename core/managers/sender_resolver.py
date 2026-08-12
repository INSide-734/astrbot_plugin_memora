"""发送者名称解析旧路径兼容导出。"""

from ..features.conversation.application.sender_resolver import (
    _format_raw_user_name,
    _iter_raw_sender_candidates,
    _normalize_sender_name,
    _raw_get,
    _resolve_sender_name,
)

__all__ = [
    "_format_raw_user_name",
    "_iter_raw_sender_candidates",
    "_normalize_sender_name",
    "_raw_get",
    "_resolve_sender_name",
]
