"""跨 feature 共享的时间规范化与读取侧 as-of 原语。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

TIME_SOURCES = frozenset({"explicit", "metadata", "ingested", "derived", "unknown"})
TIME_PRECISIONS = frozenset({"instant", "day", "unknown"})


def normalize_datetime(value: datetime | None) -> datetime | None:
    """将时间规范化为 UTC；无时区时间按 UTC 兼容解释。"""

    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("时间值必须是 datetime 或 None")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    """解析 ISO 文本、datetime 或 Unix 秒；无法确认时返回 None。"""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return normalize_datetime(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return normalize_datetime(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def infer_time_precision(value: Any) -> str:
    """根据原始时间表示区分日期级和精确时刻。"""

    if isinstance(value, datetime) or isinstance(value, (int, float)):
        return "instant"
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
            return "day" if parse_datetime(text) is not None else "unknown"
        return "instant" if parse_datetime(text) is not None else "unknown"
    return "unknown"


def serialize_datetime(value: datetime | None) -> str | None:
    """将时间序列化为稳定的 UTC ISO 文本。"""

    normalized = normalize_datetime(value)
    return normalized.isoformat() if normalized else None


def normalize_reference_time(value: datetime | None) -> datetime | None:
    """规范化查询 as-of 时间；未知值保持 None。"""

    return normalize_datetime(value)


def reference_time_key(value: Any) -> str:
    """生成可安全加入检索缓存键的时间值。"""

    return serialize_datetime(parse_datetime(value)) or "current"


def visible_at(
    reference_time: datetime,
    *,
    occurred_at: datetime | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    invalid_at: datetime | None = None,
    require_occurred: bool = False,
) -> bool:
    """判断 source 或派生对象在闭区间 as-of 时刻是否可见。"""

    current = normalize_datetime(reference_time)
    if current is None:
        return False
    occurred = normalize_datetime(occurred_at)
    if require_occurred and occurred is None:
        return False
    if occurred is not None and occurred > current:
        return False
    start = normalize_datetime(valid_from)
    end = normalize_datetime(valid_to)
    if start is not None and end is not None and end < start:
        return False
    invalid = normalize_datetime(invalid_at)
    if invalid is not None and current >= invalid:
        return False
    return (start is None or current >= start) and (end is None or current <= end)


def canonical_visible_at(metadata: Any, reference_time: datetime | None) -> bool:
    """过滤已知发生在 as-of 之后的 canonical 候选。"""

    if reference_time is None or not isinstance(metadata, dict):
        return True
    occurred = parse_datetime(
        metadata.get("occurred_at")
        or metadata.get("event_time")
        or metadata.get("timestamp")
    )
    ingested = parse_datetime(metadata.get("create_time"))
    if occurred is not None and occurred > normalize_datetime(reference_time):
        return False
    if (
        occurred is None
        and ingested is not None
        and ingested > normalize_datetime(reference_time)
    ):
        return False
    return True


def validate_time_labels(time_source: str, time_precision: str) -> None:
    """校验时间来源和精度枚举。"""

    if time_source not in TIME_SOURCES:
        raise ValueError("unknown time_source")
    if time_precision not in TIME_PRECISIONS:
        raise ValueError("unknown time_precision")


__all__ = [
    "TIME_PRECISIONS",
    "TIME_SOURCES",
    "canonical_visible_at",
    "infer_time_precision",
    "normalize_datetime",
    "normalize_reference_time",
    "parse_datetime",
    "reference_time_key",
    "serialize_datetime",
    "validate_time_labels",
    "visible_at",
]
