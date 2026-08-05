"""反馈排序候选权重的只读 baseline/shadow 离线消融。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models.feedback_signal import FeedbackSignalAggregate, FeedbackSignalPolicy
from .feedback_learning_evidence import (
    EvidenceEvaluatorConfig,
    FeedbackRankingConfigSnapshot,
    FeedbackRankingReplayManifest,
    LatencyEvidence,
    LearningEvidenceArtifact,
    QualityMetricEvidence,
    build_feedback_ranking_replay_manifest,
    build_learning_evidence,
    feedback_ranking_case_hash,
    feedback_ranking_dataset_hash,
    feedback_ranking_snapshot_hash,
    feedback_replay_window_hash,
    feedback_stage_timing_map,
    parse_evidence_utc_timestamp,
    supported_feedback_evaluator_config,
    validate_feedback_ranking_manifest,
    validate_feedback_ranking_snapshots,
    validate_learning_evidence,
)
from .feedback_learning_evidence_contract import (
    ALLOWED_EVIDENCE_REGRESSION_FAILURES,
    complete_evidence_regression_checks,
    valid_evidence_binding,
)
from .retrieval_quality import EvaluationCase, ndcg_at_k, recall_at_k, reciprocal_rank

_EVALUATOR_VERSION = "feedback-ranking-evidence-v1"


@dataclass(frozen=True, slots=True)
class FeedbackRankingMetrics:
    """单个反馈排序变体的质量、延迟和成本聚合。"""

    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    observed_p50_latency_ms: float | None
    observed_p95_latency_ms: float | None
    annotated_p50_latency_ms: float | None
    annotated_p95_latency_ms: float | None
    observed_provider_calls: float | None = None
    observed_token_cost: float | None = None


@dataclass(frozen=True, slots=True)
class FeedbackRankingPairedSample:
    """保存同一匿名问题的检索 stage、在线 TTFT 与成本配对观测。"""

    case_hash: str
    observed_at_utc: str
    baseline_stage_latencies_ms: tuple[tuple[str, float], ...]
    shadow_stage_latencies_ms: tuple[tuple[str, float], ...]
    baseline_ttft_ms: float | None
    shadow_ttft_ms: float | None
    baseline_provider_calls: float | None
    shadow_provider_calls: float | None
    baseline_token_cost: float | None
    shadow_token_cost: float | None


@dataclass(frozen=True, slots=True)
class FeedbackRankingEvidenceRequest:
    """绑定一次离线回放及其完整问题级配对样本。"""

    aggregation_revision: str
    quality_gate_version: str
    replay_manifest: FeedbackRankingReplayManifest
    baseline_snapshot: FeedbackRankingConfigSnapshot
    target_snapshot: FeedbackRankingConfigSnapshot
    evaluator_config: EvidenceEvaluatorConfig
    independent_window_count: int
    paired_samples: tuple[FeedbackRankingPairedSample, ...]
    regression_checks: tuple[str, ...]
    regression_failures: tuple[str, ...] = ()


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
    evidence_status: str = "unavailable"
    evidence_reason_codes: tuple[str, ...] = ("evidence_not_requested",)
    evidence_artifact: LearningEvidenceArtifact | None = None


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
    evidence_request: FeedbackRankingEvidenceRequest | None = None,
) -> FeedbackRankingReport:
    """比较 baseline/shadow，并在完整配对观测存在时构建不可变证据。"""

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
        baseline_latencies = _latencies(case, started, "baseline")
        baseline_rows.append(_Row(case, baseline_candidates, *baseline_latencies))
        if top_reason == "accepted" and aggregate is not None:
            shadow_candidates = _apply_shadow_weights(
                baseline_candidates,
                aggregate,
                active_policy,
                safe_k,
            )
        else:
            shadow_candidates = list(baseline_candidates)
        shadow_latencies = (
            _latencies(case, started, "shadow")
            if top_reason == "accepted"
            else baseline_latencies
        )
        shadow_rows.append(_Row(case, shadow_candidates, *shadow_latencies))

    baseline_metrics = _metrics(baseline_rows, safe_k)
    shadow_metrics = _metrics(shadow_rows, safe_k)
    evidence_artifact, evidence_status, evidence_reasons = _build_evidence_artifact(
        baseline_rows,
        shadow_rows,
        k=safe_k,
        top_reason=top_reason,
        aggregate=aggregate,
        policy=active_policy,
        request=evidence_request,
    )
    delta = (
        aggregate.delta_from_baseline if aggregate and top_reason == "accepted" else 0.0
    )
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
        evidence_status=evidence_status,
        evidence_reason_codes=evidence_reasons,
        evidence_artifact=evidence_artifact,
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
    observed_latency_ms: float
    annotated_latency_ms: float | None


def _normalize_candidates(items: Sequence[Any] | None) -> list[_Candidate]:
    """规范化候选的 doc ID、有限分数和固定 route。"""

    result: list[_Candidate] = []
    for item in list(items or []):
        if isinstance(item, Mapping):
            doc_id = item.get("doc_id") or item.get("id") or item.get("memory_id")
            score = item.get("final_score", item.get("score", 0.0))
            metadata = (
                item.get("metadata")
                if isinstance(item.get("metadata"), Mapping)
                else {}
            )
            route = str(item.get("route") or metadata.get("route") or "document")
        else:
            doc_id = getattr(item, "doc_id", item)
            score = getattr(item, "final_score", getattr(item, "score", 0.0))
            metadata = getattr(item, "metadata", {}) or {}
            route = str(
                getattr(item, "route", None) or metadata.get("route") or "document"
            )
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

    document_ratio = (
        aggregate.proposed_document_weight / policy.baseline_document_weight
    )
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
    observed_latencies: list[float] = []
    annotated_latencies: list[float] = []
    for row in rows:
        recall, mrr, ndcg = _row_quality(row, k)
        recalls.append(recall)
        mrrs.append(mrr)
        ndcgs.append(ndcg)
        observed_latencies.append(row.observed_latency_ms)
        if row.annotated_latency_ms is not None:
            annotated_latencies.append(row.annotated_latency_ms)
    return FeedbackRankingMetrics(
        recall_at_k=_mean(recalls),
        mrr=_mean(mrrs),
        ndcg_at_k=_mean(ndcgs),
        observed_p50_latency_ms=_percentile(observed_latencies, 50),
        observed_p95_latency_ms=_percentile(observed_latencies, 95),
        annotated_p50_latency_ms=_percentile(annotated_latencies, 50),
        annotated_p95_latency_ms=_percentile(annotated_latencies, 95),
    )


def _build_evidence_artifact(
    baseline_rows: Sequence[_Row],
    shadow_rows: Sequence[_Row],
    *,
    k: int,
    top_reason: str,
    aggregate: FeedbackSignalAggregate | None,
    policy: FeedbackSignalPolicy,
    request: FeedbackRankingEvidenceRequest | None,
) -> tuple[LearningEvidenceArtifact | None, str, tuple[str, ...]]:
    """验证同窗配对观测并返回 ready/rejected/unavailable artifact。"""

    if request is None:
        return None, "unavailable", ("evidence_not_requested",)
    if top_reason != "accepted" or aggregate is None:
        return None, "unavailable", ("evidence_ablation_not_accepted",)
    if not _valid_evidence_binding(request):
        return None, "unavailable", ("evidence_binding_invalid",)
    if not supported_feedback_evaluator_config(request.evaluator_config):
        return None, "unavailable", ("evidence_evaluator_config_invalid",)
    if request.independent_window_count != aggregate.independent_window_count:
        return None, "unavailable", ("evidence_window_count_mismatch",)
    if not validate_feedback_ranking_manifest(
        request.replay_manifest,
        [row.case for row in baseline_rows],
    ):
        return None, "unavailable", ("evidence_manifest_mismatch",)
    if not _snapshots_match_evaluation(request, aggregate, policy):
        return None, "unavailable", ("evidence_config_snapshot_mismatch",)

    row_hashes = [feedback_ranking_case_hash(row.case.case_id) for row in baseline_rows]
    if not row_hashes or len(row_hashes) != len(set(row_hashes)):
        return None, "unavailable", ("evidence_case_set_invalid",)
    samples = list(request.paired_samples)
    if not samples or any(
        not _valid_paired_sample(
            item,
            stage_names=request.evaluator_config.retrieval_stage_names,
        )
        for item in samples
    ):
        return None, "unavailable", ("evidence_sample_invalid",)
    sample_hashes = [item.case_hash for item in samples]
    if len(sample_hashes) != len(set(sample_hashes)):
        return None, "unavailable", ("evidence_sample_set_invalid",)
    if set(sample_hashes) != set(row_hashes):
        return None, "unavailable", ("evidence_sample_set_mismatch",)
    if any(
        item not in ALLOWED_EVIDENCE_REGRESSION_FAILURES
        for item in request.regression_failures
    ):
        return None, "unavailable", ("evidence_regression_reason_invalid",)

    baseline_by_hash = {
        feedback_ranking_case_hash(row.case.case_id): row for row in baseline_rows
    }
    shadow_by_hash = {
        feedback_ranking_case_hash(row.case.case_id): row for row in shadow_rows
    }
    if set(shadow_by_hash) != set(baseline_by_hash):
        return None, "unavailable", ("evidence_case_set_invalid",)
    ordered_hashes = sorted(row_hashes)
    dataset_hash = feedback_ranking_dataset_hash(request.replay_manifest)
    sample_by_hash = {item.case_hash: item for item in samples}
    ordered_samples = [sample_by_hash[item] for item in ordered_hashes]
    replay_window_hash = feedback_replay_window_hash(
        dataset_hash,
        ordered_samples,
    )
    quality_metrics = _quality_evidence(
        baseline_by_hash,
        shadow_by_hash,
        ordered_hashes,
        k=k,
        aggregation_revision=request.aggregation_revision,
        dataset_hash=dataset_hash,
        replay_window_hash=replay_window_hash,
        evaluator_config=request.evaluator_config,
    )

    latency_metrics = tuple(
        _latency_evidence(
            stage_name,
            [
                dict(item.baseline_stage_latencies_ms)[stage_name]
                for item in ordered_samples
            ],
            [
                dict(item.shadow_stage_latencies_ms)[stage_name]
                for item in ordered_samples
            ],
        )
        for stage_name in request.evaluator_config.retrieval_stage_names
    ) + (
        _latency_evidence(
            "ttft",
            [item.baseline_ttft_ms for item in ordered_samples],
            [item.shadow_ttft_ms for item in ordered_samples],
        ),
    )
    failures = set(request.regression_failures)
    if _group_recall_gap(shadow_rows, k) > _group_recall_gap(baseline_rows, k):
        failures.add("group_recall_gap_regression")
    if _has_negative_case_regression(baseline_rows, shadow_rows, k):
        failures.add("negative_case_regression")

    artifact = build_learning_evidence(
        aggregation_revision=request.aggregation_revision,
        source_config_revision=request.baseline_snapshot.source_config_revision,
        quality_gate_version=request.quality_gate_version,
        dataset_hash=dataset_hash,
        replay_window_hash=replay_window_hash,
        evaluator_version=_EVALUATOR_VERSION,
        evaluation_k=k,
        evaluator_config=request.evaluator_config,
        baseline_snapshot_hash=feedback_ranking_snapshot_hash(
            request.baseline_snapshot
        ),
        target_snapshot_hash=feedback_ranking_snapshot_hash(request.target_snapshot),
        sample_count=len(ordered_samples),
        independent_window_count=request.independent_window_count,
        quality_metrics=quality_metrics,
        latency_metrics=latency_metrics,
        baseline_provider_calls=_mean(
            [float(item.baseline_provider_calls) for item in ordered_samples]
        ),
        candidate_provider_calls=_mean(
            [float(item.shadow_provider_calls) for item in ordered_samples]
        ),
        baseline_token_cost=_mean(
            [float(item.baseline_token_cost) for item in ordered_samples]
        ),
        candidate_token_cost=_mean(
            [float(item.shadow_token_cost) for item in ordered_samples]
        ),
        regression_checks=request.regression_checks,
        regression_failures=tuple(sorted(failures)),
    )
    gate = validate_learning_evidence(
        artifact,
        aggregation_revision=request.aggregation_revision,
        source_config_revision=request.baseline_snapshot.source_config_revision,
        quality_gate_version=request.quality_gate_version,
    )
    return (
        artifact,
        "ready" if gate.passed else "rejected",
        gate.reason_codes,
    )


def _valid_evidence_binding(request: FeedbackRankingEvidenceRequest) -> bool:
    """校验 artifact revision、manifest、快照和窗口结构。"""

    return isinstance(request, FeedbackRankingEvidenceRequest) and (
        isinstance(request.replay_manifest, FeedbackRankingReplayManifest)
        and isinstance(request.baseline_snapshot, FeedbackRankingConfigSnapshot)
        and isinstance(request.target_snapshot, FeedbackRankingConfigSnapshot)
        and valid_evidence_binding(
            request.aggregation_revision,
            request.baseline_snapshot.source_config_revision,
            request.quality_gate_version,
        )
        and isinstance(request.evaluator_config, EvidenceEvaluatorConfig)
        and isinstance(request.independent_window_count, int)
        and not isinstance(request.independent_window_count, bool)
        and request.independent_window_count > 0
        and isinstance(request.paired_samples, tuple)
        and complete_evidence_regression_checks(request.regression_checks)
        and isinstance(request.regression_failures, tuple)
        and all(isinstance(item, str) for item in request.regression_failures)
    )


def _snapshots_match_evaluation(
    request: FeedbackRankingEvidenceRequest,
    aggregate: FeedbackSignalAggregate,
    policy: FeedbackSignalPolicy,
) -> bool:
    """核对快照 revision、归一化权重与本次实际 baseline/target。"""

    return validate_feedback_ranking_snapshots(
        request.baseline_snapshot,
        request.target_snapshot,
        baseline_weights=(
            policy.baseline_document_weight,
            policy.baseline_graph_weight,
        ),
        target_weights=(
            aggregate.proposed_document_weight,
            aggregate.proposed_graph_weight,
        ),
    )


def _valid_paired_sample(
    sample: object,
    *,
    stage_names: tuple[str, ...],
) -> bool:
    """要求每个问题的 stage、TTFT 和成本 baseline/shadow 均存在。"""

    if (
        not isinstance(sample, FeedbackRankingPairedSample)
        or len(sample.case_hash) != 64
        or any(char not in "0123456789abcdef" for char in sample.case_hash)
    ):
        return False
    if parse_evidence_utc_timestamp(sample.observed_at_utc) is None:
        return False
    baseline_stages = feedback_stage_timing_map(sample.baseline_stage_latencies_ms)
    shadow_stages = feedback_stage_timing_map(sample.shadow_stage_latencies_ms)
    if baseline_stages is None or shadow_stages is None:
        return False
    if set(baseline_stages) != set(stage_names) or set(shadow_stages) != set(
        stage_names
    ):
        return False
    return all(
        _finite_nonnegative(value)
        for value in (
            *baseline_stages.values(),
            *shadow_stages.values(),
            sample.baseline_ttft_ms,
            sample.shadow_ttft_ms,
            sample.baseline_provider_calls,
            sample.shadow_provider_calls,
            sample.baseline_token_cost,
            sample.shadow_token_cost,
        )
    )


def _finite_nonnegative(value: object) -> bool:
    """仅接受非布尔、有限且非负的普通数值。"""

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _quality_evidence(
    baseline_by_hash: Mapping[str, _Row],
    shadow_by_hash: Mapping[str, _Row],
    ordered_hashes: Sequence[str],
    *,
    k: int,
    aggregation_revision: str,
    dataset_hash: str,
    replay_window_hash: str,
    evaluator_config: EvidenceEvaluatorConfig,
) -> tuple[QualityMetricEvidence, ...]:
    """按匿名问题对构造三项质量均值及确定性 paired bootstrap CI。"""

    baseline_values = [
        _row_quality(baseline_by_hash[item], k) for item in ordered_hashes
    ]
    shadow_values = [_row_quality(shadow_by_hash[item], k) for item in ordered_hashes]
    metrics: list[QualityMetricEvidence] = []
    for index, name in enumerate(("Recall@K", "MRR", "nDCG")):
        baseline = [item[index] for item in baseline_values]
        shadow = [item[index] for item in shadow_values]
        ci_low, ci_high = _paired_bootstrap_ci(
            baseline,
            shadow,
            seed_material=(
                f"{dataset_hash}:{replay_window_hash}:{aggregation_revision}:{k}:{name}"
            ),
            iterations=evaluator_config.bootstrap_iterations,
            confidence_level=evaluator_config.confidence_level,
        )
        metrics.append(
            QualityMetricEvidence(
                name=name,
                baseline=_mean(baseline),
                candidate=_mean(shadow),
                ci_low=ci_low,
                ci_high=ci_high,
            )
        )
    return tuple(metrics)


def _paired_bootstrap_ci(
    baseline: Sequence[float],
    shadow: Sequence[float],
    *,
    seed_material: str,
    iterations: int,
    confidence_level: float,
) -> tuple[float, float]:
    """对问题对差值执行确定性重采样并返回配置的双侧置信区间。"""

    if not baseline or len(baseline) != len(shadow):
        raise ValueError("paired_bootstrap_samples_invalid")
    differences = [candidate - control for control, candidate in zip(baseline, shadow)]
    seed_payload = json.dumps(
        {"seed": seed_material, "differences": differences},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    seed = int.from_bytes(
        hashlib.sha256(seed_payload.encode("utf-8")).digest()[:8],
        "big",
    )
    generator = random.Random(seed)
    size = len(differences)
    estimates = [
        sum(differences[generator.randrange(size)] for _ in range(size)) / size
        for _ in range(iterations)
    ]
    tail = (1.0 - confidence_level) * 50.0
    return (
        float(_percentile(estimates, tail, digits=6)),
        float(_percentile(estimates, 100.0 - tail, digits=6)),
    )


def _latency_evidence(
    name: str,
    baseline_values: Sequence[float | None],
    shadow_values: Sequence[float | None],
) -> LatencyEvidence:
    """把完整配对观测分别聚合为 retrieval stage 或 TTFT p50/p95。"""

    baseline = [float(item) for item in baseline_values if item is not None]
    shadow = [float(item) for item in shadow_values if item is not None]
    baseline_p50 = _percentile(baseline, 50)
    baseline_p95 = _percentile(baseline, 95)
    shadow_p50 = _percentile(shadow, 50)
    shadow_p95 = _percentile(shadow, 95)
    if None in (baseline_p50, baseline_p95, shadow_p50, shadow_p95):
        raise ValueError("latency_samples_invalid")
    return LatencyEvidence(
        name=name,
        baseline_p50_ms=float(baseline_p50),
        baseline_p95_ms=float(baseline_p95),
        candidate_p50_ms=float(shadow_p50),
        candidate_p95_ms=float(shadow_p95),
    )


def _row_quality(row: _Row, k: int) -> tuple[float, float, float]:
    """计算单个问题的 Recall@K、MRR 与 nDCG。"""

    ranked = [item.doc_id for item in row.candidates]
    if row.case.metadata.get("expected_no_hit") is True:
        score = 1.0 if not ranked[:k] else 0.0
        return score, score, score
    return (
        recall_at_k(ranked, row.case.relevant_doc_ids, k=k),
        reciprocal_rank(ranked[:k], row.case.relevant_doc_ids),
        ndcg_at_k(ranked, row.case.relevant_doc_ids, k=k),
    )


def _has_negative_case_regression(
    baseline_rows: Sequence[_Row],
    shadow_rows: Sequence[_Row],
    k: int,
) -> bool:
    """检测 baseline 正确而 shadow 失败的匿名负例回归。"""

    shadow_by_hash = {
        feedback_ranking_case_hash(row.case.case_id): row for row in shadow_rows
    }
    for baseline in baseline_rows:
        if baseline.case.metadata.get("expected_no_hit") is not True:
            continue
        case_hash = feedback_ranking_case_hash(baseline.case.case_id)
        shadow = shadow_by_hash.get(case_hash)
        if shadow is None:
            return True
        if _row_quality(baseline, k)[0] > _row_quality(shadow, k)[0]:
            return True
    return False


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


def _latencies(
    case: EvaluationCase, started: float, branch: str
) -> tuple[float, float | None]:
    """返回墙钟实测延迟和可选的分支人工标注延迟。"""

    observed = max(0.0, (time.perf_counter() - started) * 1000)
    raw = case.metadata.get(f"annotated_{branch}_latency_ms")
    try:
        annotated = (
            float(raw) if raw is not None and not isinstance(raw, bool) else None
        )
    except (TypeError, ValueError):
        annotated = None
    if annotated is not None and (not math.isfinite(annotated) or annotated < 0.0):
        annotated = None
    return observed, annotated


def _mean(values: Sequence[float]) -> float:
    """计算有限均值。"""

    return round(sum(values) / len(values), 4) if values else 0.0


def _percentile(
    values: Sequence[float],
    percentile: float,
    *,
    digits: int = 4,
) -> float | None:
    """计算确定性线性百分位。"""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], digits)
    weight = position - lower
    return round(
        ordered[lower] * (1 - weight) + ordered[upper] * weight,
        digits,
    )


__all__ = [
    "build_feedback_ranking_replay_manifest",
    "feedback_ranking_case_hash",
    "FeedbackRankingConfigSnapshot",
    "FeedbackRankingEvidenceRequest",
    "FeedbackRankingMetrics",
    "FeedbackRankingPairedSample",
    "FeedbackRankingReplayManifest",
    "FeedbackRankingReport",
    "run_feedback_ranking_ablation",
]
