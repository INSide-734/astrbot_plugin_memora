"""Topic catalog 持久化对象的最小安全投影。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TopicCatalogSource:
    """当前 canonical source 可用于构建目录的最小快照。"""

    memory_id: int
    source_revision: str
    scope_key: str
    chat_type: str
    privacy_level: str
    resolver_revision: str
    topics: tuple[tuple[str, str], ...]
    observed_at: float


@dataclass(frozen=True, slots=True)
class TopicCatalogDirty:
    """目录 dirty queue 的安全投影，不携带旧 source 数据。"""

    dirty_id: int
    memory_id: int
    operation: str
    sequence: int
    state: str
    attempt_count: int
    lease_until: float | None
    last_error_code: str | None


__all__ = ["TopicCatalogDirty", "TopicCatalogSource"]
