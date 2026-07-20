"""Memory Evolution 派生存储使用的纯转换与安全解析 helper。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..models.memory_evolution import (
    DerivedState,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
)

_PRIVACY_ORDER = {"public": 0, "shared": 1, "confidential": 2}


def _dt(value: datetime | None) -> str | None:
    """将 UTC datetime 转换为 SQLite 文本。"""

    return value.astimezone(timezone.utc).isoformat() if value else None


def _metadata_dict(value) -> dict:
    """将 documents.metadata 安全解析为字典。"""

    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _privacy_allowed(item_level: str, requested_level: str) -> bool:
    """判断 canonical 隐私等级是否允许写入目标派生对象。"""

    item = _PRIVACY_ORDER.get(item_level)
    requested = _PRIVACY_ORDER.get(requested_level)
    return item is not None and requested is not None and item <= requested


def _relation(row) -> RelationView:
    """将 SQLite relation 行映射为领域视图。"""

    return RelationView(
        row["relation_id"],
        int(row["source_memory_id"]),
        int(row["target_memory_id"]),
        RelationType(row["relation_type"]),
        float(row["confidence"]),
        row["scope_key"],
        row["privacy_level"],
        DerivedState(row["state"]),
        row["source_revision"],
        row["target_revision"],
        _parse(row["valid_from"]),
        _parse(row["valid_to"]),
    )


def _projection(row, source_ids: tuple[int, ...]) -> ProjectionView:
    """将 SQLite projection 行映射为领域视图。"""

    return ProjectionView(
        row["projection_id"],
        ProjectionType(row["projection_type"]),
        row["summary"],
        source_ids,
        row["scope_key"],
        row["privacy_level"],
        float(row["confidence"]),
        DerivedState(row["state"]),
        _parse(row["valid_from"]),
        _parse(row["valid_to"]),
    )


def _parse(value: str | None) -> datetime | None:
    """将 SQLite 时间文本解析为 datetime。"""

    if not value:
        return None
    return datetime.fromisoformat(value)


__all__ = [
    "_PRIVACY_ORDER",
    "_dt",
    "_metadata_dict",
    "_parse",
    "_privacy_allowed",
    "_projection",
    "_relation",
]
