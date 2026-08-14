"""自主学习 status 中 reload operation 的低敏只读视图。"""

from __future__ import annotations

import re
from collections.abc import Mapping

_OPAQUE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22,128}\Z", re.ASCII)
_REVISION_PATTERN = re.compile(r"[\x21-\x7e]{1,128}\Z", re.ASCII)
_REASON_PATTERN = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)
_RELOAD_STATES = frozenset(
    {"queued", "running", "succeeded", "failed", "restart_required"}
)


def learning_reload_view(
    value: object,
    *,
    runtime_matches_persisted: bool,
) -> dict[str, str]:
    """优先返回持久化 operation；仅无 operation 时按权重差异回退。

    Args:
        value: manager 原子快照中的 ``reload`` 记录。
        runtime_matches_persisted: 当前运行时权重是否匹配持久化配置。

    Returns:
        仅包含 operation ID、状态、稳定原因和可选配置 revision 的视图。
    """

    if value is None:
        return _inferred_reload_view(runtime_matches_persisted)
    if not isinstance(value, Mapping):
        return {"state": "unknown", "reason_code": "reload_state_invalid"}
    operation_id = value.get("operation_id")
    state = value.get("state")
    reason_code = value.get("reason_code")
    if (
        not isinstance(operation_id, str)
        or _OPAQUE_ID_PATTERN.fullmatch(operation_id) is None
        or state not in _RELOAD_STATES
        or not isinstance(reason_code, str)
        or _REASON_PATTERN.fullmatch(reason_code) is None
    ):
        return {"state": "unknown", "reason_code": "reload_state_invalid"}
    result = {
        "operation_id": operation_id,
        "state": str(state),
        "reason_code": reason_code,
    }
    applied_revision = value.get("applied_revision")
    if (
        isinstance(applied_revision, str)
        and _REVISION_PATTERN.fullmatch(applied_revision) is not None
    ):
        result["applied_revision"] = applied_revision
    return result


def _inferred_reload_view(runtime_matches_persisted: bool) -> dict[str, str]:
    """在确实没有持久化 operation 时生成保守的权重差异视图。"""

    if runtime_matches_persisted:
        return {"state": "unknown", "reason_code": "reload_not_observed"}
    return {"state": "restart_required", "reason_code": "runtime_config_stale"}


__all__ = ["learning_reload_view"]
