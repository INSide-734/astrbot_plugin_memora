"""canonical 提交后派生调度所需的无正文事件契约。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..temporal import normalize_datetime

_PRIVACY_LEVELS = frozenset({"public", "shared", "confidential"})
_SOURCE_ROLES = frozenset({"primary", "supporting"})


def _required(value: Any, name: str) -> str:
    """校验事件中的非空文本字段。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_required")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CanonicalMemoryCommitted:
    """表示 canonical 事务成功后的无正文提交通知。

    事件只允许携带标量和字段名，不得携带 query、prompt、正文、ID 列表或
    Provider 凭据。``idempotency_key`` 固定为 event/consumer/revision 的上游
    可组合部分，具体 consumer 由派生工作发布器补齐。
    """

    event_id: str
    op_id: str
    memory_id: int
    revision: str
    scope_key: str
    privacy: str
    stable_user_id: str
    source_role: str
    changed_fields: tuple[str, ...]
    content_digest: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        """校验事件标量、固定隐私枚举和内容摘要格式。"""

        for value, name in (
            (self.event_id, "event_id"),
            (self.op_id, "op_id"),
            (self.revision, "revision"),
            (self.scope_key, "scope_key"),
            (self.stable_user_id, "stable_user_id"),
            (self.content_digest, "content_digest"),
        ):
            _required(value, name)
        if (
            isinstance(self.memory_id, bool)
            or not isinstance(self.memory_id, int)
            or self.memory_id <= 0
        ):
            raise ValueError("memory_id_invalid")
        if self.privacy not in _PRIVACY_LEVELS:
            raise ValueError("privacy_invalid")
        if self.source_role not in _SOURCE_ROLES:
            raise ValueError("source_role_invalid")
        fields = tuple(
            dict.fromkeys(
                field.strip()
                for field in self.changed_fields
                if isinstance(field, str) and field.strip()
            )
        )
        if not fields or any(len(field) > 128 for field in fields):
            raise ValueError("changed_fields_invalid")
        if len(self.content_digest) != 64:
            raise ValueError("content_digest_invalid")
        try:
            int(self.content_digest, 16)
        except (TypeError, ValueError) as exc:
            raise ValueError("content_digest_invalid") from exc
        occurred_at = normalize_datetime(self.occurred_at)
        if occurred_at is None:
            raise ValueError("occurred_at_required")
        object.__setattr__(self, "changed_fields", fields)
        object.__setattr__(self, "occurred_at", occurred_at)

    @property
    def event_revision_key(self) -> str:
        """返回供 outbox 去重使用的事件与修订组合键。"""

        return f"{self.event_id}:{self.revision}"

    @staticmethod
    def digest_content(content: str) -> str:
        """计算正文摘要；调用方不得把正文放入事件本身。"""

        if not isinstance(content, str):
            raise TypeError("content must be text")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__ = ["CanonicalMemoryCommitted"]
