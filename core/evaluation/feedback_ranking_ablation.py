"""反馈排序候选权重的只读 baseline/shadow 离线消融。"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models.feedback_signal import FeedbackSignalAggregate, FeedbackSignalPolicy
from .retrieval_quality import EvaluationCase, ndcg_at_k, recall_at_k, reciprocal_rank


@dataclass(frozen=True, slots=True)
class FeedbackRankingMetrics:
    """单个反馈排序变体的质量、延迟和成本聚合。"""

    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    provider_calls: float
    token_cost: float


@dataclass(frozen=True, slots=True)
class FeedbackRankingReport:
    """不含事件、domain、query 或文档 ID 的安全实验报告。"""

    status: str
    reason_code: str
    total_cases: int
    k: int
    baseline: FeedbackRankingMetrics
    shadow: FeedbackRankingMetrics
    weight_delta: float
    attack_drift: float
    max_group_recall_gap: float
    reason_code_aggregates: dict[str, int] = field(default_factory=dict)


async def run_feedback_ranking_ablation(
    cases: Sequence[EvaluationCase],
    baseline_retriever: Callable[
        [EvaluationCase, int], Sequence[Any] | Awaitable[Sequence[Any]]
    ],
    aggregate: FeedbackSignalAggregate | None,
    *,
    k: int,
    policy: FeedbackSignalPolicy | None = None,
    prerequisite_met: bool = True,
) -> FeedbackRankingReport:
    """比较 baseline 和隔离 shadow 权重，普通信号不足时保留 baseline。"""

    active_policy = policy or FeedbackSignalPolicy()
    safe_k = max(1, min(int(k), 20))
    baseline_rows: list[_Row] = []
    shadow_rows: list[_Row] = []
    if not prerequisite_met:
        aggregate = None
        top_reason = "evaluation_prerequisite_unmet"
    elif aggregate is None or aggregate.status != "candidate":
        top_reason = "baseline_retained"
    elif abs(aggregate.delta_from_baseline) > active_policy.max_weight_delta:
        top_reason = "weight_delta_capped"
    elif not all(_domain_matches(case, aggregate) for case in cases):
        top_reason = "scope_mismatch"
    else:
        top_reason = "accepted"

    for case in cases:
        started = time.perf_counter()
        raw = baseline_retriever(case, safe_k)
        try:
            raw = await raw if inspect.isawaitable(raw) else raw
        except asyncio.CancelledError:
            raise
        baseline_candidates = _normalize_candidates(raw)
        baseline_latency = _latency(case, started, "baseline")
        baseline_rows.append(
            _Row(case, baseline_candidates, baseline_latency)
        )
        if top_reason == "accepted" and aggregate is not None:
            shadow_candidates = _apply_shadow_weights(
                baseline_candidates,
                aggregate,
                active_policy,
                safe_k,
            )
        else:
            shadow_candidates = list(baseline_candidates)
        shadow_latency = (
            _latency(case, started, "shadow")
            if top_reason == "accepted"
            else baseline_latency
        )
        shadow_rows.append(
            _Row(case, shadow_candidates, shadow_latency)
        )

    baseline_metrics = _metrics(baseline_rows, safe_k)
    shadow_metrics = _metrics(shadow_rows, safe_k)
    delta = aggregate.delta_from_baseline if aggregate and top_reason == "accepted" else 0.0
    return FeedbackRankingReport(
        status="completed" if top_reason == "accepted" else "skipped",
        reason_code=top_reason,
        total_cases=len(cases),
        k=safe_k,
        baseline=baseline_metrics,
        shadow=shadow_metrics,
        weight_delta=round(delta, 6),
        attack_drift=round(abs(delta), 6),
        max_group_recall_gap=_group_recall_gap(shadow_rows, safe_k),
        reason_code_aggregates={top_reason: len(cases)},
    )


@dataclass(frozen=True, slots=True)
class _Candidate:
    """内部候选结构，不进入报告。"""

    doc_id: str
    score: float
    route: str


@dataclass(frozen=True, slots=True)
class _Row:
    """单分支评测行。"""

    case: EvaluationCase
    candidates: list[_Candidate]
    latency_ms: float


def _normalize_candidates(items: Sequence[Any] | None) -> list[_Candidate]:
    """规范化候选的 doc ID、有限分数和固定 route。"""

    result: list[_Candidate] = []
    for item in list(items or []):
        if isinstance(item, Mapping):
            doc_id = item.get("doc_id") or item.get("id") or item.get("memory_id")
            score = item.get("final_score", item.get("score", 0.0))
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            route = str(item.get("route") or metadata.get("route") or "document")
        else:
            doc_id = getattr(item, "doc_id", item)
            score = getattr(item, "final_score", getattr(item, "score", 0.0))
            metadata = getattr(item, "metadata", {}) or {}
            route = str(getattr(item, "route", None) or metadata.get("route") or "document")
        try:
            number = float(score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number) or route not in {"document", "graph"}:
            continue
        if str(doc_id or "").strip():
            result.append(_Candidate(str(doc_id).strip(), number, route))
    return result


def _apply_shadow_weights(
    candidates: Sequence[_Candidate],
    aggregate: FeedbackSignalAggregate,
    policy: FeedbackSignalPolicy,
    k: int,
) -> list[_Candidate]:
    """只在内存中应用文档/图路权重比，不修改候选或 live retriever。"""

    document_ratio = aggregate.proposed_document_weight / policy.baseline_document_weight
    graph_ratio = aggregate.proposed_graph_weight / policy.baseline_graph_weight
    adjusted = [
        _Candidate(
            item.doc_id,
            item.score * (document_ratio if item.route == "document" else graph_ratio),
            item.route,
        )
        for item in candidates
    ]
    return sorted(adjusted, key=lambda item: (-item.score, item.doc_id))[:k]


def _domain_matches(
    case: EvaluationCase,
    aggregate: FeedbackSignalAggregate,
) -> bool:
    """要求匿名用例的可信 scope/persona 与聚合精确一致。"""

    return (
        case.metadata.get("scope_domain") == aggregate.scope_domain
        and case.metadata.get("persona_domain") == aggregate.persona_domain
    )


def _metrics(rows: Sequence[_Row], k: int) -> FeedbackRankingMetrics:
    """聚合现有 Recall@K、MRR、nDCG 和匿名延迟。"""

    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    for row in rows:
        ranked = [item.doc_id for item in row.candidates]
        if row.case.metadata.get("expected_no_hit") is True:
            score = 1.0 if not ranked[:k] else 0.0
            recalls.append(score)
            mrrs.append(score)
            ndcgs.append(score)
        else:
            recalls.append(recall_at_k(ranked, row.case.relevant_doc_ids, k=k))
            mrrs.append(reciprocal_rank(ranked, row.case.relevant_doc_ids))
            ndcgs.append(ndcg_at_k(ranked, row.case.relevant_doc_ids, k=k))
        latencies.append(row.latency_ms)
    return FeedbackRankingMetrics(
        _mean(recalls),
        _mean(mrrs),
        _mean(ndcgs),
        _percentile(latencies, 50),
        _percentile(latencies, 95),
        0.0,
        0.0,
    )


def _group_recall_gap(rows: Sequence[_Row], k: int) -> float:
    """计算匿名 fixture 分组的最大 Recall 差异，不输出分组名称。"""

    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        label = str(row.case.metadata.get("group_label") or "default")
        groups[label].append(
            recall_at_k(
                [item.doc_id for item in row.candidates],
                row.case.relevant_doc_ids,
                k=k,
            )
        )
    means = [_mean(values) for values in groups.values()]
    return round(max(means) - min(means), 4) if means else 0.0


def _latency(case: EvaluationCase, started: float, branch: str) -> float:
    """读取匿名分支延迟，缺失时使用本地测量。"""

    fallback = (time.perf_counter() - started) * 1000
    try:
        return max(0.0, float(case.metadata.get(f"{branch}_latency_ms", fallback)))
    except (TypeError, ValueError):
        return max(0.0, fallback)


def _mean(values: Sequence[float]) -> float:
    """计算有限均值。"""

    return round(sum(values) / len(values), 4) if values else 0.0


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    """计算确定性线性百分位。"""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


__all__ = [
    "FeedbackRankingMetrics",
    "FeedbackRankingReport",
    "run_feedback_ranking_ablation",
]
