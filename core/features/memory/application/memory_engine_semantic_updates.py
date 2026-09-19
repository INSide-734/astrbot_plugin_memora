"""识别会改变 canonical 派生证据的语义 metadata 更新。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from ....shared.number_utils import safe_float

# 这些字段由召回访问和写入协调过程维护，不参与 Profile/Knowledge/Note
# 的内容判断。其余字段默认按语义字段处理，避免新增受支持字段时静默漏调度。
_OPERATIONAL_METADATA_KEYS = frozenset(
    {
        "access_count",
        "last_access_time",
        "last_recall_time",
        "last_retrieved_at",
        "recall_count",
        "retrieval_count",
        "updated_at",
    }
)


def has_semantic_metadata_change(
    current_metadata: Mapping[str, Any], updates: Mapping[str, Any]
) -> bool:
    """判断 metadata 更新是否改变派生 proposal 依赖的语义值。

    访问计数、访问时间和写入时间是运行态字段，即使它们推进 canonical
    revision，也不应重新消耗派生 proposal 的预算。字段值未发生变化时同样
    返回 ``False``，避免重复提交相同的语义 metadata 产生无效任务。
    """

    for key, value in updates.items():
        if key in _OPERATIONAL_METADATA_KEYS:
            continue
        if current_metadata.get(key) != value:
            return True
    return False


def prepare_semantic_metadata_update(
    current_metadata: Mapping[str, Any],
    updates: dict[str, Any],
    *,
    observed_at: float,
) -> bool:
    """标记 topic 实际变化时间并返回是否发生语义更新。

    参数：
        current_metadata: 更新前的 canonical metadata。
        updates: 将写入的 metadata 增量；topic 变化时原地补入观察时间。
        observed_at: 当前 canonical 提交使用的 Unix 时间。

    返回：
        任一非运行态字段发生变化时返回 ``True``。
    """

    changed = has_semantic_metadata_change(current_metadata, updates)
    if "topics" in updates and updates.get("topics") != current_metadata.get("topics"):
        updates["topic_observed_at"] = observed_at
    return changed


__all__ = [
    "apply_interference_maintenance_delta",
    "apply_reinforcement_maintenance_delta",
    "has_semantic_metadata_change",
    "is_runtime_maintenance_delta",
    "prepare_semantic_metadata_update",
]

# 内部运行态维护只允许改写这些字段：它们影响召回时的强化/干扰表现，但不改变
# 事实内容、主体、scope/privacy 或有效期语义，因此不推进 source revision。
_RUNTIME_MAINTENANCE_KEYS: Final = frozenset(
    {"importance", "revised_by", "reinforcement_count", "ttl_days"}
)

_MAX_REINFORCEMENT_TTL_MULTIPLIER: Final = 2.0
_REINFORCEMENT_TTL_GROWTH: Final = 1.05
_DEFAULT_TTL_DAYS: Final = 30.0
_INTERFERENCE_IMPORTANCE_FACTOR: Final = 0.9
_MIN_INTERFERENCE_IMPORTANCE: Final = 0.05


def is_runtime_maintenance_delta(
    current_metadata: Mapping[str, Any], delta: Mapping[str, Any]
) -> bool:
    """校验增量只写入运行态白名单字段，且确实产生了新值。

    白名单之外或与当前值相同的字段一律拒绝：调用方只能表达已经识别的运行态
    维护意图，不能借维护入口改写事实、证据、scope/privacy 或生命周期字段。
    """

    if not delta:
        return True
    for key, value in delta.items():
        if key not in _RUNTIME_MAINTENANCE_KEYS:
            return False
        if current_metadata.get(key) == value:
            return False
    return True


def apply_reinforcement_maintenance_delta(
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """按当前运行态计算测试效应强化增量。

    计数与 TTL 都从传入的最新 metadata 推导，沿用既有 ``min(2x, 1.05^n)``
    上限规则；返回的字段集合固定，调用方不得再拼接整份 metadata。
    """

    current_count = int(safe_float(metadata.get("reinforcement_count", 0) or 0, 0.0))
    original_ttl = safe_float(metadata.get("ttl_days"), _DEFAULT_TTL_DAYS)
    if original_ttl <= 0:
        original_ttl = _DEFAULT_TTL_DAYS
    new_count = current_count + 1
    new_ttl = min(
        original_ttl * _MAX_REINFORCEMENT_TTL_MULTIPLIER,
        original_ttl * (_REINFORCEMENT_TTL_GROWTH**new_count),
    )
    return {
        "reinforcement_count": new_count,
        "ttl_days": round(new_ttl, 2),
    }


def apply_interference_maintenance_delta(
    metadata: Mapping[str, Any],
    *,
    source_memory_id: int,
) -> dict[str, Any]:
    """按当前运行态计算逆行干扰衰减增量。

    ``importance`` 沿用既有 10% 衰减与 0.05 下限规则，``revised_by`` 记录本次
    干扰来源；两者都不改变 canonical 内容与授权语义，因此不推进 revision。
    """

    importance = safe_float(metadata.get("importance"), 0.5)
    return {
        "importance": round(
            max(
                _MIN_INTERFERENCE_IMPORTANCE,
                importance * _INTERFERENCE_IMPORTANCE_FACTOR,
            ),
            4,
        ),
        "revised_by": int(source_memory_id),
    }
