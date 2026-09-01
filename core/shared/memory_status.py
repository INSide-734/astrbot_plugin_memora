"""canonical memory 生命周期状态的读取、可见性与写入契约。"""

from __future__ import annotations

import math
from collections.abc import Mapping, MutableMapping
from typing import Any, Final

MEMORY_STATUS_ACTIVE: Final = "active"
MEMORY_STATUS_DORMANT: Final = "dormant"
MEMORY_STATUS_ARCHIVED: Final = "archived"
MEMORY_STATUS_DELETED: Final = "deleted"
MEMORY_STATUS_UNKNOWN: Final = "unknown"
_MEMORY_STATUS_WRITE_VALUES: Final = frozenset(
    {
        MEMORY_STATUS_ACTIVE,
        MEMORY_STATUS_DORMANT,
        MEMORY_STATUS_ARCHIVED,
        MEMORY_STATUS_DELETED,
    }
)
_SUMMARY_SOURCE_ORPHAN: Final = "summary_source_orphan"


_ACTIVE_MEMORY_STATUS_ALIASES: Final = frozenset({"current", "stable"})
_STATUS_ASCII_WHITESPACE: Final = " \t\n\r\v\f"
_ASCII_UPPER_TO_LOWER: Final = str.maketrans(
    {chr(value): chr(value + 32) for value in range(ord("A"), ord("Z") + 1)}
)


def _normalize_status_text(value: str) -> str:
    """按 SQLite 的生命周期状态规则裁剪 ASCII 空白并映射 ASCII 大小写。

    状态枚举只包含 ASCII 值；故意不使用 Unicode ``casefold()``，以保证 SQL
    页面筛选与 Python 的状态读取对未知旧值也得到相同结果。
    """

    return value.strip(_STATUS_ASCII_WHITESPACE).translate(_ASCII_UPPER_TO_LOWER)


def effective_memory_status(
    metadata: Mapping[str, Any] | None,
    *,
    default: str = MEMORY_STATUS_ACTIVE,
) -> str:
    """返回 metadata 中当前生效的生命周期状态。

    参数：
        metadata: canonical memory 的 metadata 映射；无效映射安全回退。
        default: 两个状态字段均缺失时返回的规范状态。

    返回：
        优先读取 ``memory_status``、再读取旧字段 ``status`` 的规范状态；旧版
        ``current``、``stable`` 统一归一为 ``active``。字段缺失时返回
        ``default``；字段存在但均不是可识别状态时返回 ``unknown``，避免将
        损坏来源伪装为活跃记忆。文本仅按 SQLite 相同的 ASCII 空白与大小写规则
        规范化。
    """

    if isinstance(metadata, Mapping):
        has_status_field = False
        for field in ("memory_status", "status"):
            if field not in metadata:
                continue
            has_status_field = True
            value = metadata.get(field)
            if not isinstance(value, str):
                continue
            normalized = _normalize_status_text(value)
            if normalized in _ACTIVE_MEMORY_STATUS_ALIASES:
                return MEMORY_STATUS_ACTIVE
            if normalized in _MEMORY_STATUS_WRITE_VALUES:
                return normalized
        if has_status_field:
            return MEMORY_STATUS_UNKNOWN
    return _normalize_status_text(default) or MEMORY_STATUS_ACTIVE


def is_memory_recallable(metadata: Mapping[str, Any] | None) -> bool:
    """判断 canonical memory 是否可进入召回候选。

    参数：
        metadata: canonical memory 的 metadata 映射。

    返回：
        仅 ``active`` 与其旧版别名可召回；总结来源已失效的 orphan、休眠、
        归档、已删除及未知状态均返回 ``False``。
    """

    return bool(
        effective_memory_status(metadata) == MEMORY_STATUS_ACTIVE
        and not (
            isinstance(metadata, Mapping)
            and metadata.get(_SUMMARY_SOURCE_ORPHAN) is True
        )
    )


def is_memory_active(metadata: Mapping[str, Any] | None) -> bool:
    """判断 canonical memory 是否处于可参与派生的活跃状态。

    参数：
        metadata: canonical memory 的 metadata 映射。

    返回：
        ``active`` 且不是总结来源 orphan 时返回 ``True``；其余状态返回
        ``False``，避免从失效来源生成派生知识。
    """

    return is_memory_recallable(metadata)


def set_memory_status(
    metadata: MutableMapping[str, Any],
    status: str,
    *,
    status_changed_at: float,
) -> None:
    """原子地同步写入生命周期状态、兼容字段与转换时间。

    参数：
        metadata: 将被原地更新的 canonical memory metadata。
        status: 仅允许 ``active``、``dormant``、``archived`` 或 ``deleted``。
        status_changed_at: 本次状态转换的有限 Unix 时间戳。

    异常：
        ValueError: 状态或转换时间非法时抛出。
    """

    normalized = _normalize_status_text(status) if isinstance(status, str) else ""
    if normalized not in _MEMORY_STATUS_WRITE_VALUES:
        raise ValueError("memory_status_invalid")
    if (
        isinstance(status_changed_at, bool)
        or not isinstance(status_changed_at, (int, float))
        or not math.isfinite(status_changed_at)
    ):
        raise ValueError("memory_status_changed_at_invalid")
    metadata["memory_status"] = normalized
    metadata["status"] = normalized
    metadata["status_changed_at"] = float(status_changed_at)


__all__ = [
    "MEMORY_STATUS_ACTIVE",
    "MEMORY_STATUS_ARCHIVED",
    "MEMORY_STATUS_DELETED",
    "MEMORY_STATUS_DORMANT",
    "MEMORY_STATUS_UNKNOWN",
    "effective_memory_status",
    "is_memory_active",
    "is_memory_recallable",
    "set_memory_status",
]
