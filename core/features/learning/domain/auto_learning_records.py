"""自主学习状态机共享的记录校验、迁移和低敏结果辅助。"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .auto_learning_actions import (
    is_opaque_id,
    new_opaque_id,
    normalize_weights,
    stable_revision,
    weight_snapshot_hash,
)

CANDIDATE_STATUSES = frozenset(
    {
        "ready_for_review",
        "rejected",
        "published",
        "stale",
        "recovery_required",
        "rolled_back",
        "invalid_state",
    }
)
CANDIDATE_REASONS = frozenset(
    {
        "candidate",
        "insufficient_evidence",
        "conflicting_evidence",
        "quality_gate_failed",
        "published",
        "stale",
        "recovery_required",
        "rolled_back",
        "legacy_migrated",
        "invalid_state",
    }
)


def publish_failure(
    reason_code: str,
    *,
    recovery_required: bool = False,
    config_applied: bool = False,
    applied_revision: str | None = None,
) -> dict[str, Any]:
    """构造固定字段的发布失败结果。"""

    result: dict[str, Any] = {"published": False, "reason_code": reason_code}
    if recovery_required:
        result["recovery_required"] = True
    if config_applied:
        result["config_applied"] = True
    if applied_revision is not None:
        result["applied_revision"] = applied_revision
    return result


def rollback_failure(
    reason_code: str,
    *,
    recovery_required: bool = False,
    config_applied: bool = False,
) -> dict[str, Any]:
    """构造固定字段的回滚失败结果。"""

    result: dict[str, Any] = {"restored": False, "reason_code": reason_code}
    if recovery_required:
        result["recovery_required"] = True
    if config_applied:
        result["config_applied"] = True
    return result


def operation_key(action: str, candidate_id: str, revision: str) -> str:
    """为动作、候选和请求 revision 生成稳定幂等键。"""

    return stable_revision(
        "learning-operation-v1",
        {"action": action, "candidate_id": candidate_id, "revision": revision},
    )


def mapping_records(value: object) -> dict[str, dict[str, Any]]:
    """只接受字符串键到字典记录的状态集合。"""

    if not isinstance(value, dict):
        raise ValueError("learning_state_collection_invalid")
    if not all(
        isinstance(key, str) and isinstance(item, dict) for key, item in value.items()
    ):
        raise ValueError("learning_state_collection_invalid")
    return value


def normalize_candidate(candidate_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """把磁盘候选裁剪为固定字段；非法数值降级为 invalid_state。"""

    if not is_opaque_id(candidate_id) or raw.get("candidate_id") != candidate_id:
        raise ValueError("learning_candidate_id_invalid")
    allowed = {
        "candidate_id",
        "candidate_key",
        "candidate_scope",
        "aggregation_revision",
        "source_config_revision",
        "evidence_revision",
        "quality_gate_version",
        "baseline_snapshot_hash",
        "target_snapshot_hash",
        "proposed_document_weight",
        "proposed_graph_weight",
        "delta_from_baseline",
        "accepted_count",
        "independent_window_count",
        "decayed_support",
        "window_set",
        "status",
        "reason_code",
        "policy_version",
        "created_at",
    }
    candidate = {key: copy.deepcopy(raw.get(key)) for key in allowed}
    weights = normalize_weights(
        {
            "document_route_weight": candidate["proposed_document_weight"],
            "graph_route_weight": candidate["proposed_graph_weight"],
        }
    )
    valid_counts = all(
        isinstance(candidate[name], int)
        and not isinstance(candidate[name], bool)
        and candidate[name] >= 0
        for name in ("accepted_count", "independent_window_count")
    )
    valid_strings = all(
        isinstance(candidate[name], str) and candidate[name]
        for name in (
            "candidate_key",
            "candidate_scope",
            "aggregation_revision",
            "source_config_revision",
            "evidence_revision",
            "quality_gate_version",
            "baseline_snapshot_hash",
            "target_snapshot_hash",
            "created_at",
        )
    )
    if (
        weights is None
        or not valid_counts
        or not finite_between(candidate["decayed_support"], 0.0, 1.0)
        or not finite_between(candidate["delta_from_baseline"], -0.4, 0.4)
        or not valid_strings
        or candidate["status"] not in CANDIDATE_STATUSES
        or candidate["reason_code"] not in CANDIDATE_REASONS
    ):
        return invalid_candidate(candidate_id)
    candidate["proposed_document_weight"] = weights["document_route_weight"]
    candidate["proposed_graph_weight"] = weights["graph_route_weight"]
    window_set = candidate.get("window_set")
    candidate["window_set"] = (
        list(window_set)
        if isinstance(window_set, list)
        and all(isinstance(item, str) for item in window_set)
        else []
    )
    return candidate


def invalid_candidate(candidate_id: str) -> dict[str, Any]:
    """生成不携带污染字段的安全无效候选。"""

    return {
        "candidate_id": candidate_id,
        "candidate_key": "invalid",
        "candidate_scope": "global_aggregate",
        "aggregation_revision": "invalid",
        "source_config_revision": "invalid",
        "evidence_revision": "invalid",
        "quality_gate_version": "invalid",
        "baseline_snapshot_hash": "invalid",
        "target_snapshot_hash": "invalid",
        "proposed_document_weight": 0.7,
        "proposed_graph_weight": 0.3,
        "delta_from_baseline": 0.0,
        "accepted_count": 0,
        "independent_window_count": 0,
        "decayed_support": 0.0,
        "window_set": [],
        "status": "invalid_state",
        "reason_code": "invalid_state",
        "policy_version": 1,
        "created_at": "invalid",
    }


def legacy_candidate(
    candidate_id: str,
    old_key: str,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """把旧 scoped candidate 降级为不可发布但可追踪的迁移记录。"""

    weights = normalize_weights(
        {
            "document_route_weight": raw.get(
                "proposed_document_weight", raw.get("document_route_weight")
            ),
            "graph_route_weight": raw.get(
                "proposed_graph_weight", raw.get("graph_route_weight")
            ),
        }
    )
    if weights is None:
        return invalid_candidate(candidate_id)
    return {
        "candidate_id": candidate_id,
        "candidate_key": stable_revision("legacy-candidate-key", old_key),
        "candidate_scope": "global_aggregate",
        "aggregation_revision": stable_revision("legacy-aggregation", old_key),
        "source_config_revision": str(raw.get("revision", "legacy")),
        "evidence_revision": "legacy-unverified",
        "quality_gate_version": "legacy-unverified",
        "baseline_snapshot_hash": weight_snapshot_hash(weights),
        "target_snapshot_hash": weight_snapshot_hash(weights),
        "proposed_document_weight": weights["document_route_weight"],
        "proposed_graph_weight": weights["graph_route_weight"],
        "delta_from_baseline": 0.0,
        "accepted_count": 0,
        "independent_window_count": 0,
        "decayed_support": 0.0,
        "window_set": [],
        "status": "stale",
        "reason_code": "legacy_migrated",
        "policy_version": 1,
        "created_at": str(raw.get("published_at", raw.get("created_at", utc_now()))),
    }


def legacy_publication(
    publication_id: str,
    candidate_id: str,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """把旧单快照转换为可显式回滚的 publication。"""

    before = normalize_weights(
        {
            "document_route_weight": raw.get("previous_document_weight"),
            "graph_route_weight": raw.get("previous_graph_weight"),
        }
    )
    after = normalize_weights(raw)
    if before is None or after is None:
        raise ValueError("learning_legacy_publication_invalid")
    return {
        "publication_id": publication_id,
        "publication_revision": publication_id,
        "parent_publication_revision": None,
        "candidate_id": candidate_id,
        "before_config_hash": weight_snapshot_hash(before),
        "after_config_hash": weight_snapshot_hash(after),
        "before_weight_hash": weight_snapshot_hash(before),
        "after_weight_hash": weight_snapshot_hash(after),
        "before_document_weight": before["document_route_weight"],
        "before_graph_weight": before["graph_route_weight"],
        "after_document_weight": after["document_route_weight"],
        "after_graph_weight": after["graph_route_weight"],
        "requested_revision": str(raw.get("revision", "legacy")),
        "applied_revision": str(raw.get("revision", "legacy")),
        "evidence_revision": "legacy-unverified",
        "aggregation_revision": "legacy-unverified",
        "quality_gate_version": "legacy-unverified",
        "status": "active",
        "published_at": str(raw.get("published_at", utc_now())),
    }


def legacy_intent(
    operation_id: str,
    candidate_id: str,
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    """把旧 prepared intent 转换为显式恢复所需的最小记录。"""

    publication = legacy_publication(new_opaque_id(), candidate_id, raw)
    return {
        "operation_id": operation_id,
        "action": "publish",
        "phase": "recovery_required",
        "candidate_id": candidate_id,
        "publication_revision": publication["publication_revision"],
        "parent_publication_revision": None,
        "requested_revision": publication["requested_revision"],
        "before_config_hash": publication["before_config_hash"],
        "before_weight_hash": publication["before_weight_hash"],
        "before_document_weight": publication["before_document_weight"],
        "before_graph_weight": publication["before_graph_weight"],
        "target_weight_hash": publication["after_weight_hash"],
        "target_document_weight": publication["after_document_weight"],
        "target_graph_weight": publication["after_graph_weight"],
        "aggregation_revision": "legacy-unverified",
        "source_config_revision": publication["requested_revision"],
        "evidence_revision": "legacy-unverified",
        "quality_gate_version": "legacy-unverified",
        "created_at": str(raw.get("created_at", utc_now())),
    }


def publication_view(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """裁剪 active publication 为 API 可进一步使用的低敏 allowlist。"""

    if value is None:
        return None
    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "publication_revision",
            "parent_publication_revision",
            "candidate_id",
            "requested_revision",
            "applied_revision",
            "status",
            "published_at",
        )
    }


def candidate_status_view(value: Mapping[str, Any]) -> dict[str, Any]:
    """裁剪候选为原子 status 所需的低敏动作句柄与统计字段。"""

    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "candidate_id",
            "source_config_revision",
            "proposed_document_weight",
            "proposed_graph_weight",
            "delta_from_baseline",
            "accepted_count",
            "independent_window_count",
            "decayed_support",
            "status",
            "reason_code",
        )
    }


def claim_view(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """裁剪 operation claim，避免 candidate binding 或权重进入 status。"""

    if value is None:
        return None
    return {
        "operation_id": value.get("operation_id"),
        "action": value.get("action"),
        "created_at": value.get("created_at"),
    }


def safe_status(value: object) -> str:
    """把未知候选状态收敛为固定 invalid_state。"""

    return (
        value
        if isinstance(value, str) and value in CANDIDATE_STATUSES
        else "invalid_state"
    )


def safe_reason(value: object) -> str:
    """把未知候选原因收敛为固定 invalid_state。"""

    return (
        value
        if isinstance(value, str) and value in CANDIDATE_REASONS
        else "invalid_state"
    )


def finite_between(value: object, minimum: float, maximum: float) -> bool:
    """判断普通数值是否有限且位于闭区间内。"""

    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def parse_datetime(value: object) -> datetime | None:
    """解析已持久化 ISO 时间；失败时返回空以使用当前 UTC 时间。"""

    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def utc_now() -> str:
    """返回带时区的当前 UTC ISO 时间。"""

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CANDIDATE_REASONS",
    "CANDIDATE_STATUSES",
    "claim_view",
    "legacy_candidate",
    "legacy_intent",
    "legacy_publication",
    "mapping_records",
    "normalize_candidate",
    "operation_key",
    "parse_datetime",
    "publication_view",
    "publish_failure",
    "rollback_failure",
    "safe_reason",
    "safe_status",
    "utc_now",
]
