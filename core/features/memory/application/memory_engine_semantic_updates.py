"""识别会改变 canonical 派生证据的语义 metadata 更新。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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


__all__ = ["has_semantic_metadata_change", "prepare_semantic_metadata_update"]
