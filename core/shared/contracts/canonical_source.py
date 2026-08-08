"""canonical memory 来源和只读授权读取契约。

本模块只依赖标准库与现有无状态时间工具。它不打开数据库、不读取插件资源，
也不把缺失的授权信息解释成默认允许；平台和 feature 实现应通过本模块的
request/result 边界交换来源证据。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from ...models.temporal import normalize_datetime, validate_time_labels

_PRIVACY_LEVELS = frozenset({"public", "shared", "confidential"})
_USER_ROLES = frozenset({"user", "assistant", "system", "admin", "owner"})
_SOURCE_ROLES = frozenset({"primary", "supporting"})
_MAX_EVIDENCE_CHARS = 4_000
_MAX_READ_CHARS = 16_000


def _require_text(value: str, name: str) -> str:
    """校验必需文本并返回原值。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_required")
    return value.strip()


def _check_interval(start: datetime | None, end: datetime | None) -> None:
    """拒绝结束时间早于开始时间的来源区间。"""

    normalized_start = normalize_datetime(start)
    normalized_end = normalize_datetime(end)
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_end < normalized_start
    ):
        raise ValueError("valid_interval_invalid")


@dataclass(frozen=True)
class MemorySourceRef:
    """作为 proposal 证据的 canonical memory 快照。

    ``content`` 只在已通过 source-reader 授权的本地边界内出现；持久化来源
    映射默认省略正文。``stable_user_id`` 是可选的可信身份证据，用于兼容
    历史记录中尚未保存该字段的来源。
    """

    memory_id: int
    revision_token: str
    scope_key: str
    privacy_level: str
    occurred_at: datetime
    content: str | None = None
    reference_at: datetime | None = None
    ingested_at: datetime | None = None
    time_source: str = "unknown"
    time_precision: str = "unknown"
    source_role: str = "primary"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    topic_keys: tuple[str, ...] = ()
    subject_key: str | None = None
    stable_user_id: str | None = None

    def __post_init__(self) -> None:
        """校验来源主键、修订、隐私、时间和证据正文上限。"""

        if (
            isinstance(self.memory_id, bool)
            or not isinstance(self.memory_id, int)
            or self.memory_id <= 0
        ):
            raise ValueError("memory_id must be a positive integer")
        _require_text(self.revision_token, "revision_token")
        _require_text(self.scope_key, "scope_key")
        if self.privacy_level not in _PRIVACY_LEVELS:
            raise ValueError("privacy_level must be public, shared, or confidential")
        if self.source_role not in _SOURCE_ROLES:
            raise ValueError("source_role must be primary or supporting")
        if self.content is not None:
            if not isinstance(self.content, str):
                raise ValueError("content must be a string or None")
            if len(self.content) > _MAX_EVIDENCE_CHARS:
                raise ValueError("content exceeds the evidence length limit")
        if self.stable_user_id is not None:
            _require_text(self.stable_user_id, "stable_user_id")
        occurred_at = normalize_datetime(self.occurred_at)
        if occurred_at is None:
            raise ValueError("occurred_at_required")
        object.__setattr__(self, "occurred_at", occurred_at)
        reference_at = (
            occurred_at
            if self.reference_at is None
            else normalize_datetime(self.reference_at)
        )
        object.__setattr__(self, "reference_at", reference_at)
        object.__setattr__(self, "ingested_at", normalize_datetime(self.ingested_at))
        _check_interval(self.valid_from, self.valid_to)
        object.__setattr__(self, "valid_from", normalize_datetime(self.valid_from))
        object.__setattr__(self, "valid_to", normalize_datetime(self.valid_to))
        normalized_topics = tuple(
            dict.fromkeys(
                topic.strip()[:128]
                for topic in self.topic_keys
                if isinstance(topic, str) and topic.strip()
            )
        )[:32]
        object.__setattr__(self, "topic_keys", normalized_topics)
        if self.subject_key is not None:
            object.__setattr__(
                self,
                "subject_key",
                _require_text(self.subject_key, "subject_key")[:128],
            )
        validate_time_labels(self.time_source, self.time_precision)

    @property
    def revision(self) -> str:
        """返回沿用旧命名的修订别名。"""

        return self.revision_token


class SourceReadDenyReason(StrEnum):
    """source-reader 失败时对外公开的稳定拒绝原因。"""

    INVALID_REQUEST = "invalid_request"
    PRIVACY_DENIED = "privacy_denied"
    SCOPE_MISMATCH = "scope_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    ROLE_DENIED = "role_denied"
    REVISION_MISMATCH = "revision_mismatch"
    CONTENT_LIMIT = "content_limit"
    SOURCE_UNAVAILABLE = "source_unavailable"
    METADATA_MISMATCH = "metadata_mismatch"


@dataclass(frozen=True)
class SourceReadRequest:
    """描述一次 fail-closed 的 canonical 来源读取请求。

    ``expected_revisions`` 既指定待读取的 memory ID，也固定每条来源的修订
    token。实现不得在缺失该映射时返回正文；若只需验证请求格式，可传空映射，
    但读取结果仍应为空。
    """

    scope_key: str
    privacy_clearance: str
    stable_user_id: str
    user_role: str
    source_role: str = "primary"
    max_content_chars: int = _MAX_EVIDENCE_CHARS
    expected_revisions: Mapping[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验授权上下文和每个来源的修订约束。"""

        if not isinstance(self.scope_key, str) or not self.scope_key.strip():
            raise ValueError(SourceReadDenyReason.INVALID_REQUEST.value)
        if self.privacy_clearance not in _PRIVACY_LEVELS:
            raise ValueError(SourceReadDenyReason.INVALID_REQUEST.value)
        if not isinstance(self.stable_user_id, str) or not self.stable_user_id.strip():
            raise ValueError(SourceReadDenyReason.INVALID_REQUEST.value)
        if self.user_role not in _USER_ROLES:
            raise ValueError(SourceReadDenyReason.ROLE_DENIED.value)
        if self.source_role not in _SOURCE_ROLES:
            raise ValueError(SourceReadDenyReason.ROLE_DENIED.value)
        if (
            isinstance(self.max_content_chars, bool)
            or not isinstance(self.max_content_chars, int)
            or not 0 < self.max_content_chars <= _MAX_READ_CHARS
        ):
            raise ValueError(SourceReadDenyReason.CONTENT_LIMIT.value)
        raw_revisions = self.expected_revisions
        if not isinstance(raw_revisions, Mapping):
            raise ValueError(SourceReadDenyReason.REVISION_MISMATCH.value)
        normalized: dict[int, str] = {}
        for memory_id, revision in raw_revisions.items():
            if (
                isinstance(memory_id, bool)
                or not isinstance(memory_id, int)
                or memory_id <= 0
                or not isinstance(revision, str)
                or not revision.strip()
            ):
                raise ValueError(SourceReadDenyReason.REVISION_MISMATCH.value)
            normalized[memory_id] = revision.strip()
        object.__setattr__(self, "scope_key", self.scope_key.strip())
        object.__setattr__(self, "stable_user_id", self.stable_user_id.strip())
        object.__setattr__(self, "expected_revisions", normalized)

    @property
    def memory_ids(self) -> tuple[int, ...]:
        """返回按请求顺序稳定排序的来源 ID。"""

        return tuple(self.expected_revisions)


@dataclass(frozen=True)
class SourceReadResult:
    """source-reader 的安全结果；拒绝时绝不携带正文。"""

    sources: tuple[MemorySourceRef, ...] = ()
    deny_reason: SourceReadDenyReason | None = None

    def __post_init__(self) -> None:
        """确保授权结果与拒绝原因互斥。"""

        normalized = tuple(self.sources)
        if self.deny_reason is not None and normalized:
            raise ValueError("denied_result_must_not_include_sources")
        if self.deny_reason is None and any(
            not isinstance(source, MemorySourceRef) for source in normalized
        ):
            raise ValueError("source_result_invalid")
        object.__setattr__(self, "sources", normalized)

    @property
    def allowed(self) -> bool:
        """返回是否至少有一条授权来源。"""

        return self.deny_reason is None and bool(self.sources)

    @classmethod
    def denied(cls, reason: SourceReadDenyReason) -> "SourceReadResult":
        """构造不携带正文的稳定拒绝结果。"""

        return cls(deny_reason=reason)


class CanonicalSourceReaderPort(Protocol):
    """canonical source 的异步只读端口。"""

    async def read(
        self,
        request: SourceReadRequest,
        cancel_token: Any | None = None,
    ) -> SourceReadResult:
        """按授权请求读取来源；取消必须传播 ``CancelledError``。"""

    async def read_many(
        self,
        requests: Sequence[SourceReadRequest],
        cancel_token: Any | None = None,
    ) -> tuple[SourceReadResult, ...]:
        """批量读取来源，单条拒绝不得泄露其他条目的正文。"""


def raise_if_cancelled(cancel_token: Any | None = None) -> None:
    """在共享边界检查任务或显式取消令牌，并传播取消异常。"""

    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if current_task is not None and current_task.cancelled():
        raise asyncio.CancelledError
    if cancel_token is None:
        return
    cancelled = getattr(cancel_token, "cancelled", None)
    if callable(cancelled):
        cancelled = cancelled()
    if cancelled:
        raise asyncio.CancelledError
    is_set = getattr(cancel_token, "is_set", None)
    if callable(is_set) and is_set():
        raise asyncio.CancelledError


def to_derived_metadata_source(
    source: MemorySourceRef,
    *,
    stale: bool = False,
) -> Any:
    """把 canonical 来源转换为受限派生 metadata 来源证据。

    导入放在函数内部，避免 shared contracts 在导入期反向拉起 feature 或存储
    模块；转换结果仍保留 memory、revision、scope、privacy、role 和 stale 证据。
    """

    if not isinstance(source, MemorySourceRef):
        raise TypeError("source must be MemorySourceRef")
    from ...models.derived_metadata import DerivedMetadataSourceRef

    return DerivedMetadataSourceRef(
        memory_id=source.memory_id,
        revision_token=source.revision_token,
        trusted_scope=source.scope_key,
        privacy_level=source.privacy_level,
        source_role=source.source_role,
        stale=bool(stale),
    )


# 设计文档使用的长名称保留为类型别名，避免调用方自行创造第二套契约。
CanonicalSourceReadRequest = SourceReadRequest
CanonicalSourceReadResult = SourceReadResult

__all__ = [
    "CanonicalSourceReaderPort",
    "CanonicalSourceReadRequest",
    "CanonicalSourceReadResult",
    "MemorySourceRef",
    "SourceReadDenyReason",
    "SourceReadRequest",
    "SourceReadResult",
    "raise_if_cancelled",
    "to_derived_metadata_source",
]
