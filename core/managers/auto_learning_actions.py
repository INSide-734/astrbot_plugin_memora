"""自主学习生产候选的确定性归并、身份和版本绑定。"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..models.feedback_signal import FeedbackSignalAggregate

_OPAQUE_ID_MIN_LENGTH = 22
_OPAQUE_ID_MAX_LENGTH = 128
_OPAQUE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


@dataclass(frozen=True, slots=True)
class CandidateBinding:
    """固定候选所依赖的配置、评测产物和质量策略版本。"""

    source_config_revision: str
    evidence_revision: str
    quality_gate_version: str
    evidence_passed: bool

    def __post_init__(self) -> None:
        """拒绝缺失版本或伪布尔值，避免生成无来源候选。"""

        for value in (
            self.source_config_revision,
            self.evidence_revision,
            self.quality_gate_version,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("learning_candidate_binding_invalid")
        if not isinstance(self.evidence_passed, bool):
            raise ValueError("learning_candidate_binding_invalid")


@dataclass(frozen=True, slots=True)
class GlobalLearningCandidate:
    """可审阅的唯一全局候选及其不可变低敏绑定。"""

    candidate_id: str
    candidate_key: str
    candidate_scope: str
    aggregation_revision: str
    source_config_revision: str
    evidence_revision: str
    quality_gate_version: str
    baseline_snapshot_hash: str
    target_snapshot_hash: str
    proposed_document_weight: float
    proposed_graph_weight: float
    delta_from_baseline: float
    accepted_count: int
    independent_window_count: int
    decayed_support: float
    window_set: tuple[str, ...]
    status: str
    reason_code: str
    policy_version: int
    created_at: str

    def to_record(self) -> dict[str, Any]:
        """返回可持久化副本，调用方可增加操作状态但不得改绑定。"""

        record = asdict(self)
        record["window_set"] = list(self.window_set)
        return record


def reduce_global_candidate(
    aggregates: Sequence[FeedbackSignalAggregate],
    *,
    binding: CandidateBinding,
    min_samples: int,
    min_independent_windows: int,
    baseline_document_weight: float = 0.7,
    baseline_graph_weight: float = 0.3,
    candidate_id: str | None = None,
    created_at: datetime | None = None,
) -> GlobalLearningCandidate:
    """把全部窗口证据确定性归并为唯一全局候选。

    Args:
        aggregates: 可信反馈层生成的 scope/persona/window 聚合。
        binding: 当前配置 revision 与不可变质量证据绑定。
        min_samples: 全局最小可信样本数。
        min_independent_windows: 全局最小独立时间窗口数。
        baseline_document_weight: 生成聚合时使用的文档路基线权重。
        baseline_graph_weight: 生成聚合时使用的图路基线权重。
        candidate_id: 仅供状态迁移或确定性测试注入的既有 opaque ID。
        created_at: 候选生成时间；缺省使用当前 UTC 时间。

    Returns:
        包含稳定输入 hash、随机外部 ID 和发布资格的全局候选。

    Raises:
        ValueError: 输入为空、阈值非法、权重非法或聚合策略版本冲突。
    """

    if not aggregates:
        raise ValueError("learning_aggregate_empty")
    if (
        isinstance(min_samples, bool)
        or not isinstance(min_samples, int)
        or min_samples <= 0
        or isinstance(min_independent_windows, bool)
        or not isinstance(min_independent_windows, int)
        or min_independent_windows <= 0
    ):
        raise ValueError("learning_candidate_threshold_invalid")
    baseline = normalize_weights(
        {
            "document_route_weight": baseline_document_weight,
            "graph_route_weight": baseline_graph_weight,
        }
    )
    if baseline is None:
        raise ValueError("learning_candidate_baseline_invalid")

    ordered = sorted(aggregates, key=_aggregate_sort_key)
    policy_versions = {item.policy_version for item in ordered}
    if len(policy_versions) != 1:
        raise ValueError("learning_aggregate_policy_mismatch")
    aggregation_revision = aggregation_revision_for(ordered)
    window_set = tuple(
        sorted({_window_token(item.window_start, item.window_end) for item in ordered})
    )
    accepted_count = sum(item.accepted_count for item in ordered)
    total_events = max(1, accepted_count)
    decayed_support = round(
        sum(item.decayed_support * item.accepted_count for item in ordered)
        / total_events,
        6,
    )
    proposed_document_weight = _weighted_document_weight(ordered)
    proposed_graph_weight = round(1.0 - proposed_document_weight, 6)
    delta = round(proposed_document_weight - baseline["document_route_weight"], 6)

    directions = {
        1 if item.delta_from_baseline > 0 else -1
        for item in ordered
        if not math.isclose(item.delta_from_baseline, 0.0, abs_tol=1e-12)
    }
    if len(directions) > 1:
        status, reason_code = "rejected", "conflicting_evidence"
    elif (
        accepted_count < min_samples
        or len(window_set) < min_independent_windows
        or not directions
    ):
        status, reason_code = "rejected", "insufficient_evidence"
    elif not binding.evidence_passed:
        status, reason_code = "rejected", "quality_gate_failed"
    else:
        status, reason_code = "ready_for_review", "candidate"

    resolved_id = candidate_id or new_opaque_id()
    if not is_opaque_id(resolved_id):
        raise ValueError("learning_candidate_id_invalid")
    target = {
        "document_route_weight": proposed_document_weight,
        "graph_route_weight": proposed_graph_weight,
    }
    generated_at = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return GlobalLearningCandidate(
        candidate_id=resolved_id,
        candidate_key=f"global:{aggregation_revision}",
        candidate_scope="global_aggregate",
        aggregation_revision=aggregation_revision,
        source_config_revision=binding.source_config_revision,
        evidence_revision=binding.evidence_revision,
        quality_gate_version=binding.quality_gate_version,
        baseline_snapshot_hash=weight_snapshot_hash(baseline),
        target_snapshot_hash=weight_snapshot_hash(target),
        proposed_document_weight=proposed_document_weight,
        proposed_graph_weight=proposed_graph_weight,
        delta_from_baseline=delta,
        accepted_count=accepted_count,
        independent_window_count=len(window_set),
        decayed_support=decayed_support,
        window_set=window_set,
        status=status,
        reason_code=reason_code,
        policy_version=next(iter(policy_versions)),
        created_at=generated_at.isoformat(),
    )


def new_opaque_id() -> str:
    """生成不包含业务语义且满足 Page API 长度约束的随机 ID。"""

    return secrets.token_urlsafe(24)


def aggregation_revision_for(
    aggregates: Sequence[FeedbackSignalAggregate],
) -> str:
    """按稳定排序和 canonical 窗口内容计算全局聚合 revision。

    Args:
        aggregates: 需要绑定离线质量证据的窗口级聚合。

    Returns:
        命名空间隔离的 SHA-256 十六进制 revision。

    Raises:
        ValueError: 聚合为空或窗口时间非法。
    """

    if not aggregates:
        raise ValueError("learning_aggregate_empty")
    ordered = sorted(aggregates, key=_aggregate_sort_key)
    return stable_revision(
        "learning-aggregation-v1",
        [_canonical_aggregate(item) for item in ordered],
    )


def is_opaque_id(value: object) -> bool:
    """判断值是否为受长度限制的 ASCII URL-safe opaque ID。"""

    return (
        isinstance(value, str)
        and _OPAQUE_ID_MIN_LENGTH <= len(value) <= _OPAQUE_ID_MAX_LENGTH
        and all(char in _OPAQUE_ID_CHARS for char in value)
    )


def normalize_weights(source: Mapping[str, object]) -> dict[str, float] | None:
    """校验精确的两项有限互补权重并返回标准字段。"""

    if set(source) != {"document_route_weight", "graph_route_weight"}:
        return None
    document = _finite_weight(source.get("document_route_weight"))
    graph = _finite_weight(source.get("graph_route_weight"))
    if (
        document is None
        or graph is None
        or not math.isclose(document + graph, 1.0, abs_tol=1e-6)
    ):
        return None
    return {"document_route_weight": document, "graph_route_weight": graph}


def weight_snapshot_hash(weights: Mapping[str, object]) -> str:
    """计算两个路由权重的稳定低敏 SHA-256 快照 hash。"""

    normalized = normalize_weights(weights)
    if normalized is None:
        raise ValueError("learning_weight_snapshot_invalid")
    return stable_revision("learning-weight-v1", normalized)


def stable_revision(namespace: str, payload: object) -> str:
    """用命名空间和 canonical JSON 计算稳定 SHA-256 revision。"""

    serialized = json.dumps(
        {"namespace": namespace, "payload": payload},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _weighted_document_weight(
    aggregates: Sequence[FeedbackSignalAggregate],
) -> float:
    """按样本量和偏离中性支持度的置信强度归并目标文档权重。"""

    weighted_sum = 0.0
    confidence_sum = 0.0
    for item in aggregates:
        confidence = item.accepted_count * abs(item.decayed_support - 0.5)
        if math.isclose(confidence, 0.0, abs_tol=1e-12):
            continue
        weighted_sum += item.proposed_document_weight * confidence
        confidence_sum += confidence
    if math.isclose(confidence_sum, 0.0, abs_tol=1e-12):
        first = aggregates[0]
        return round(first.proposed_document_weight, 6)
    return round(weighted_sum / confidence_sum, 6)


def _aggregate_sort_key(item: FeedbackSignalAggregate) -> tuple[object, ...]:
    """返回不依赖输入顺序的聚合稳定排序键。"""

    return (
        item.scope_domain,
        item.persona_domain or "",
        item.window_start.astimezone(timezone.utc).isoformat(),
        item.window_end.astimezone(timezone.utc).isoformat(),
        item.policy_version,
    )


def _canonical_aggregate(item: FeedbackSignalAggregate) -> dict[str, object]:
    """把窗口聚合转换为仅供 revision 计算的 canonical 结构。"""

    return {
        "scope_domain": item.scope_domain,
        "persona_domain": item.persona_domain,
        "window": _window_token(item.window_start, item.window_end),
        "accepted_count": item.accepted_count,
        "independent_window_count": item.independent_window_count,
        "decayed_support": item.decayed_support,
        "proposed_document_weight": item.proposed_document_weight,
        "proposed_graph_weight": item.proposed_graph_weight,
        "delta_from_baseline": item.delta_from_baseline,
        "status": item.status,
        "policy_version": item.policy_version,
    }


def _window_token(start: datetime, end: datetime) -> str:
    """把带时区窗口规范化为稳定 UTC token。"""

    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("learning_aggregate_window_invalid")
    return (
        f"{start.astimezone(timezone.utc).isoformat()}/"
        f"{end.astimezone(timezone.utc).isoformat()}"
    )


def _finite_weight(value: object) -> float | None:
    """把普通数字限制在闭区间零到一，并拒绝布尔值。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and 0.0 <= numeric <= 1.0 else None


__all__ = [
    "aggregation_revision_for",
    "CandidateBinding",
    "GlobalLearningCandidate",
    "is_opaque_id",
    "new_opaque_id",
    "normalize_weights",
    "reduce_global_candidate",
    "stable_revision",
    "weight_snapshot_hash",
]
