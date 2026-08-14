"""自主学习离线质量证据的不可变 artifact 与发布门。"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from .feedback_learning_evidence_contract import (
    ALLOWED_EVIDENCE_REGRESSION_FAILURES,
    SUPPORTED_EVIDENCE_EVALUATORS,
    canonical_evidence_json_value,
    complete_evidence_regression_checks,
    evidence_sha256,
    optional_evidence_number,
    plain_evidence_int,
    positive_evidence_int,
    safe_evidence_code,
    safe_evidence_stage_name,
    valid_evidence_binding,
    valid_evidence_regression_checks,
)

_REQUIRED_QUALITY_METRICS = frozenset({"Recall@K", "MRR", "nDCG"})
_REPLAY_MANIFEST_VERSION = "feedback-ranking-replay-v1"


@dataclass(frozen=True, slots=True)
class EvidenceEvaluatorConfig:
    """封存评测器采样、置信度和生产回归阈值。"""

    bootstrap_iterations: int = 2_000
    confidence_level: float = 0.95
    max_latency_regression_ratio: float = 0.10
    max_provider_cost_regression_ratio: float = 0.05
    max_token_cost_regression_ratio: float = 0.05
    minimum_improvement_ratio: float = 0.05
    retrieval_stage_names: tuple[str, ...] = ("retrieval_stage",)


@dataclass(frozen=True, slots=True)
class FeedbackRankingConfigSnapshot:
    """保存本次实际 baseline/target 权重与来源配置 revision。"""

    source_config_revision: str
    document_route_weight: float
    graph_route_weight: float


@dataclass(frozen=True, slots=True)
class FeedbackRankingReplayManifest:
    """保存由真实用例重算的匿名 canonical replay manifest。"""

    schema_version: str
    dataset_version: str
    case_fingerprints: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class QualityMetricEvidence:
    """记录单项离线质量指标的基线、候选值和配对置信区间。"""

    name: str
    baseline: float
    candidate: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True, slots=True)
class LatencyEvidence:
    """记录一个已命名检索阶段的基线与候选 p50/p95 毫秒值。"""

    name: str
    baseline_p50_ms: float
    baseline_p95_ms: float
    candidate_p50_ms: float
    candidate_p95_ms: float


@dataclass(frozen=True, slots=True)
class LearningEvidenceArtifact:
    """封存可发布候选的匿名评测标量、绑定 revision 与完整性哈希。"""

    aggregation_revision: str
    source_config_revision: str
    quality_gate_version: str
    dataset_hash: str
    replay_window_hash: str
    evaluator_version: str
    evaluation_k: int
    evaluator_config: EvidenceEvaluatorConfig
    baseline_snapshot_hash: str
    target_snapshot_hash: str
    sample_count: int
    independent_window_count: int
    quality_metrics: tuple[QualityMetricEvidence, ...]
    latency_metrics: tuple[LatencyEvidence, ...]
    baseline_provider_calls: float | None
    candidate_provider_calls: float | None
    baseline_token_cost: float | None
    candidate_token_cost: float | None
    regression_checks: tuple[str, ...]
    regression_failures: tuple[str, ...]
    passed: bool
    evidence_revision: str


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    """表达 evidence artifact 是否通过发布门及稳定失败原因。"""

    passed: bool
    reason_codes: tuple[str, ...]


_ARTIFACT_FIELDS = frozenset(LearningEvidenceArtifact.__dataclass_fields__)
_EVALUATOR_CONFIG_FIELDS = frozenset(EvidenceEvaluatorConfig.__dataclass_fields__)
_QUALITY_FIELDS = frozenset(QualityMetricEvidence.__dataclass_fields__)
_LATENCY_FIELDS = frozenset(LatencyEvidence.__dataclass_fields__)


def build_learning_evidence(
    *,
    aggregation_revision: str,
    source_config_revision: str,
    quality_gate_version: str,
    dataset_hash: str,
    replay_window_hash: str,
    evaluator_version: str,
    sample_count: int,
    independent_window_count: int,
    quality_metrics: Sequence[QualityMetricEvidence],
    latency_metrics: Sequence[LatencyEvidence],
    baseline_token_cost: float | None,
    candidate_token_cost: float | None,
    regression_checks: Sequence[str],
    regression_failures: Sequence[str],
    evaluation_k: int = 1,
    evaluator_config: EvidenceEvaluatorConfig | None = None,
    baseline_snapshot_hash: str | None = None,
    target_snapshot_hash: str | None = None,
    baseline_provider_calls: float | None = 0.0,
    candidate_provider_calls: float | None = 0.0,
) -> LearningEvidenceArtifact:
    """构建冻结 artifact，并从完整匿名输入计算通过状态与 revision。"""

    if not valid_evidence_binding(
        aggregation_revision,
        source_config_revision,
        quality_gate_version,
    ):
        raise ValueError("learning_evidence_binding_invalid")
    if not valid_evidence_regression_checks(regression_checks):
        raise ValueError("learning_evidence_regression_checks_invalid")
    active_config = evaluator_config or EvidenceEvaluatorConfig()
    payload = {
        "aggregation_revision": aggregation_revision,
        "source_config_revision": source_config_revision,
        "quality_gate_version": quality_gate_version,
        "dataset_hash": _normalized_hash(dataset_hash, namespace="dataset"),
        "replay_window_hash": _normalized_hash(
            replay_window_hash,
            namespace="replay_window",
        ),
        "evaluator_version": evaluator_version,
        "evaluation_k": evaluation_k,
        "evaluator_config": active_config,
        "baseline_snapshot_hash": (
            baseline_snapshot_hash
            if baseline_snapshot_hash is not None
            else _canonical_hash(
                {"namespace": "baseline_snapshot", "value": source_config_revision}
            )
        ),
        "target_snapshot_hash": (
            target_snapshot_hash
            if target_snapshot_hash is not None
            else _canonical_hash(
                {"namespace": "target_snapshot", "value": source_config_revision}
            )
        ),
        "sample_count": sample_count,
        "independent_window_count": independent_window_count,
        "quality_metrics": tuple(quality_metrics),
        "latency_metrics": tuple(latency_metrics),
        "baseline_provider_calls": baseline_provider_calls,
        "candidate_provider_calls": candidate_provider_calls,
        "baseline_token_cost": baseline_token_cost,
        "candidate_token_cost": candidate_token_cost,
        "regression_checks": tuple(sorted(regression_checks)),
        "regression_failures": tuple(regression_failures),
    }
    draft = LearningEvidenceArtifact(
        **payload,
        passed=False,
        evidence_revision="",
    )
    payload["passed"] = not _content_reason_codes(draft)
    evidence_revision = _canonical_hash(payload)
    return LearningEvidenceArtifact(**payload, evidence_revision=evidence_revision)


def feedback_ranking_case_hash(case_id: str) -> str:
    """把进程内 case ID 转为只用于配对的域分离 SHA-256。"""
    payload = f"feedback-ranking-case-v1\0{str(case_id).strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_feedback_ranking_replay_manifest(
    cases: Sequence[Any],
    *,
    dataset_version: str,
) -> FeedbackRankingReplayManifest:
    """从真实用例内容构建顺序无关且不含原文的 canonical manifest。"""

    if not safe_evidence_code(dataset_version):
        raise ValueError("dataset_version_invalid")
    fingerprints = tuple(
        sorted(
            (
                feedback_ranking_case_hash(case.case_id),
                _canonical_hash(
                    {
                        "case_id": case.case_id,
                        "query": case.query,
                        "relevant_doc_ids": tuple(
                            sorted(str(item) for item in case.relevant_doc_ids)
                        ),
                        "metadata": case.metadata,
                    }
                ),
            )
            for case in cases
        )
    )
    return FeedbackRankingReplayManifest(
        schema_version=_REPLAY_MANIFEST_VERSION,
        dataset_version=dataset_version,
        case_fingerprints=fingerprints,
    )


def validate_feedback_ranking_manifest(
    manifest: object,
    cases: Sequence[Any],
) -> bool:
    """验证 manifest 结构并与本次实际用例重算结果精确比较。"""

    if not isinstance(manifest, FeedbackRankingReplayManifest):
        return False
    fingerprints = manifest.case_fingerprints
    if (
        manifest.schema_version != _REPLAY_MANIFEST_VERSION
        or not safe_evidence_code(manifest.dataset_version)
        or not isinstance(fingerprints, tuple)
        or not fingerprints
        or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and evidence_sha256(item[0])
            and evidence_sha256(item[1])
            for item in fingerprints
        )
        or len(fingerprints) != len({item[0] for item in fingerprints})
    ):
        return False
    try:
        expected = build_feedback_ranking_replay_manifest(
            cases, dataset_version=manifest.dataset_version
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return manifest == expected


def feedback_ranking_dataset_hash(manifest: FeedbackRankingReplayManifest) -> str:
    """从已验证 manifest 重算数据集哈希。"""
    return _canonical_hash(asdict(manifest))


def feedback_ranking_snapshot_hash(snapshot: FeedbackRankingConfigSnapshot) -> str:
    """从实际 revision 与两个生产权重重算配置快照哈希。"""
    return _canonical_hash(asdict(snapshot))


def validate_feedback_ranking_snapshots(
    baseline: object,
    target: object,
    *,
    baseline_weights: tuple[float, float],
    target_weights: tuple[float, float],
) -> bool:
    """核对快照 revision、归一化权重与本次实际 baseline/target。"""

    if not all(_valid_config_snapshot(item) for item in (baseline, target)):
        return False
    assert isinstance(baseline, FeedbackRankingConfigSnapshot)
    assert isinstance(target, FeedbackRankingConfigSnapshot)
    actual = (
        baseline.document_route_weight,
        baseline.graph_route_weight,
        target.document_route_weight,
        target.graph_route_weight,
    )
    return baseline.source_config_revision == target.source_config_revision and all(
        math.isclose(value, expected, abs_tol=1e-12)
        for value, expected in zip(actual, (*baseline_weights, *target_weights))
    )


def supported_feedback_evaluator_config(config: object) -> bool:
    """只接受当前固定阈值，stage 名称按实际 instrumentation 声明。"""

    if not isinstance(config, EvidenceEvaluatorConfig):
        return False
    stages = config.retrieval_stage_names
    return (
        bool(stages)
        and len(stages) == len(set(stages))
        and all(safe_evidence_stage_name(item) for item in stages)
        and config == EvidenceEvaluatorConfig(retrieval_stage_names=stages)
    )


def feedback_stage_timing_map(value: object) -> dict[str, float] | None:
    """严格恢复一条样本的 stage timing，拒绝重复名称与缺失值。"""

    if not isinstance(value, tuple):
        return None
    result: dict[str, float] = {}
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not safe_evidence_stage_name(item[0])
            or not _finite(item[1], minimum=0.0)
            or item[0] in result
        ):
            return None
        result[item[0]] = float(item[1])
    return result


def parse_evidence_utc_timestamp(value: object) -> datetime | None:
    """解析 canonical ISO UTC 时间；本地时区或模糊文本一律拒绝。"""

    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    canonical = parsed.astimezone(timezone.utc).isoformat()
    return parsed if value in {canonical, canonical.replace("+00:00", "Z")} else None


def feedback_replay_window_hash(dataset_hash: str, samples: Sequence[Any]) -> str:
    """从真实样本 UTC 边界与匿名成员重算回放窗口哈希。"""

    timestamps = [
        parse_evidence_utc_timestamp(item.observed_at_utc) for item in samples
    ]
    if not timestamps or any(item is None for item in timestamps):
        raise ValueError("replay_window_invalid")
    parsed = [item.astimezone(timezone.utc) for item in timestamps if item is not None]
    return _canonical_hash(
        {
            "dataset_hash": dataset_hash,
            "window_start_utc": min(parsed).isoformat(),
            "window_end_utc": max(parsed).isoformat(),
            "samples": tuple(
                (sample.case_hash, timestamp.isoformat())
                for sample, timestamp in zip(samples, parsed)
            ),
        }
    )


def artifact_to_record(artifact: LearningEvidenceArtifact) -> dict[str, Any]:
    """把冻结 artifact 转换为状态文件可保存的 JSON 兼容记录。"""

    if not valid_evidence_binding(
        artifact.aggregation_revision,
        artifact.source_config_revision,
        artifact.quality_gate_version,
    ) or not valid_evidence_regression_checks(artifact.regression_checks):
        raise ValueError("learning_evidence_binding_invalid")
    return json.loads(json.dumps(asdict(artifact), ensure_ascii=True, allow_nan=False))


def artifact_from_record(record: object) -> LearningEvidenceArtifact | None:
    """严格恢复状态文件中的 artifact，结构不完整时返回 ``None``。"""

    if not isinstance(record, Mapping) or set(record) != _ARTIFACT_FIELDS:
        return None
    quality = _metric_records(
        record.get("quality_metrics"),
        fields=_QUALITY_FIELDS,
        factory=QualityMetricEvidence,
    )
    latency = _metric_records(
        record.get("latency_metrics"),
        fields=_LATENCY_FIELDS,
        factory=LatencyEvidence,
    )
    evaluator_config = _evaluator_config_from_record(record.get("evaluator_config"))
    checks = record.get("regression_checks")
    failures = record.get("regression_failures")
    string_fields = (
        "aggregation_revision",
        "source_config_revision",
        "quality_gate_version",
        "dataset_hash",
        "replay_window_hash",
        "evaluator_version",
        "baseline_snapshot_hash",
        "target_snapshot_hash",
        "evidence_revision",
    )
    if (
        quality is None
        or latency is None
        or evaluator_config is None
        or not valid_evidence_regression_checks(checks)
        or not isinstance(failures, (list, tuple))
        or not all(isinstance(item, str) for item in failures)
        or not all(isinstance(record.get(name), str) for name in string_fields)
        or not plain_evidence_int(record.get("sample_count"))
        or not plain_evidence_int(record.get("independent_window_count"))
        or not plain_evidence_int(record.get("evaluation_k"))
        or not isinstance(record.get("passed"), bool)
        or not optional_evidence_number(record.get("baseline_provider_calls"))
        or not optional_evidence_number(record.get("candidate_provider_calls"))
        or not optional_evidence_number(record.get("baseline_token_cost"))
        or not optional_evidence_number(record.get("candidate_token_cost"))
        or not valid_evidence_binding(
            record.get("aggregation_revision"),
            record.get("source_config_revision"),
            record.get("quality_gate_version"),
        )
    ):
        return None
    return LearningEvidenceArtifact(
        aggregation_revision=str(record["aggregation_revision"]),
        source_config_revision=str(record["source_config_revision"]),
        quality_gate_version=str(record["quality_gate_version"]),
        dataset_hash=str(record["dataset_hash"]),
        replay_window_hash=str(record["replay_window_hash"]),
        evaluator_version=str(record["evaluator_version"]),
        evaluation_k=int(record["evaluation_k"]),
        evaluator_config=evaluator_config,
        baseline_snapshot_hash=str(record["baseline_snapshot_hash"]),
        target_snapshot_hash=str(record["target_snapshot_hash"]),
        sample_count=int(record["sample_count"]),
        independent_window_count=int(record["independent_window_count"]),
        quality_metrics=tuple(quality),
        latency_metrics=tuple(latency),
        baseline_provider_calls=record["baseline_provider_calls"],
        candidate_provider_calls=record["candidate_provider_calls"],
        baseline_token_cost=record["baseline_token_cost"],
        candidate_token_cost=record["candidate_token_cost"],
        regression_checks=tuple(checks),
        regression_failures=tuple(failures),
        passed=bool(record["passed"]),
        evidence_revision=str(record["evidence_revision"]),
    )


def validate_learning_evidence(
    artifact: LearningEvidenceArtifact,
    *,
    aggregation_revision: str,
    source_config_revision: str,
    quality_gate_version: str,
) -> EvidenceGateResult:
    """验证 artifact 完整性、候选绑定、质量回归、性能成本与最小改善门。"""

    try:
        content_reasons = _content_reason_codes(artifact)
        calculated_revision = _artifact_revision(artifact)
    except (AttributeError, TypeError, ValueError):
        return EvidenceGateResult(False, ("invalid_artifact_structure",))
    reasons = set(content_reasons)
    if artifact.evidence_revision != calculated_revision:
        reasons.add("evidence_revision_mismatch")
    if artifact.aggregation_revision != aggregation_revision:
        reasons.add("aggregation_revision_mismatch")
    if artifact.source_config_revision != source_config_revision:
        reasons.add("source_config_revision_mismatch")
    if artifact.quality_gate_version != quality_gate_version:
        reasons.add("quality_gate_version_mismatch")
    if artifact.passed != (not content_reasons):
        reasons.add("passed_mismatch")
    return EvidenceGateResult(
        passed=artifact.passed and not reasons,
        reason_codes=tuple(sorted(reasons)),
    )


def _content_reason_codes(artifact: LearningEvidenceArtifact) -> set[str]:
    """仅按 artifact 固有内容重算 Gate，不读取外部候选绑定。"""

    reasons: set[str] = set()
    if not valid_evidence_binding(
        artifact.aggregation_revision,
        artifact.source_config_revision,
        artifact.quality_gate_version,
    ):
        reasons.add("invalid_evidence_binding")
    if not evidence_sha256(artifact.dataset_hash) or not evidence_sha256(
        artifact.replay_window_hash
    ):
        reasons.add("missing_replay_binding")
    if not evidence_sha256(artifact.baseline_snapshot_hash) or not evidence_sha256(
        artifact.target_snapshot_hash
    ):
        reasons.add("invalid_snapshot_hash")
    if not artifact.evaluator_version:
        reasons.add("missing_evaluator_version")
    elif artifact.evaluator_version not in SUPPORTED_EVIDENCE_EVALUATORS:
        reasons.add("unsupported_evaluator_version")
    if not positive_evidence_int(artifact.evaluation_k) or artifact.evaluation_k > 20:
        reasons.add("invalid_evaluation_k")
    evaluator_config_valid = _valid_evaluator_config(artifact.evaluator_config)
    if not evaluator_config_valid:
        reasons.add("invalid_evaluator_config")
    active_config = (
        artifact.evaluator_config
        if evaluator_config_valid
        else EvidenceEvaluatorConfig()
    )
    if not positive_evidence_int(artifact.sample_count):
        reasons.add("invalid_sample_count")
    if not positive_evidence_int(artifact.independent_window_count):
        reasons.add("invalid_window_count")

    quality_by_name = {metric.name: metric for metric in artifact.quality_metrics}
    if (
        len(quality_by_name) != len(artifact.quality_metrics)
        or set(quality_by_name) != _REQUIRED_QUALITY_METRICS
    ):
        reasons.add("missing_quality_metric")
    for name in _REQUIRED_QUALITY_METRICS.intersection(quality_by_name):
        metric = quality_by_name[name]
        if (
            not _finite(metric.baseline, minimum=0.0)
            or not _finite(metric.candidate, minimum=0.0)
            or not _finite(metric.ci_low)
            or not _finite(metric.ci_high)
        ):
            reasons.add("invalid_quality_metric")
        elif metric.ci_low < 0.0:
            reasons.add("quality_ci_regression")

    latency_by_name = {metric.name: metric for metric in artifact.latency_metrics}
    expected_latency_names = set(active_config.retrieval_stage_names) | {"ttft"}
    if (
        len(latency_by_name) != len(artifact.latency_metrics)
        or set(latency_by_name) != expected_latency_names
    ):
        reasons.add("missing_latency_metric")
    for metric in artifact.latency_metrics:
        if not _valid_latency(metric):
            reasons.add("invalid_latency_metric")
            continue
        if _regressed(
            metric.baseline_p50_ms,
            metric.candidate_p50_ms,
            active_config.max_latency_regression_ratio,
        ):
            reasons.add("latency_p50_regression")
        if _regressed(
            metric.baseline_p95_ms,
            metric.candidate_p95_ms,
            active_config.max_latency_regression_ratio,
        ):
            reasons.add("latency_p95_regression")

    _cost_reasons(
        reasons,
        baseline=artifact.baseline_provider_calls,
        candidate=artifact.candidate_provider_calls,
        maximum_regression=active_config.max_provider_cost_regression_ratio,
        missing_reason="missing_provider_cost",
        regression_reason="provider_cost_regression",
    )
    _cost_reasons(
        reasons,
        baseline=artifact.baseline_token_cost,
        candidate=artifact.candidate_token_cost,
        maximum_regression=active_config.max_token_cost_regression_ratio,
        missing_reason="missing_token_cost",
        regression_reason="token_cost_regression",
    )
    if not complete_evidence_regression_checks(artifact.regression_checks):
        reasons.add("required_regression_checks_missing")
    if any(
        reason not in ALLOWED_EVIDENCE_REGRESSION_FAILURES
        for reason in artifact.regression_failures
    ):
        reasons.add("invalid_regression_failure")
    elif artifact.regression_failures:
        reasons.add("regression_failures_present")
    if not reasons and not _has_minimum_improvement(
        quality_by_name, latency_by_name, artifact
    ):
        reasons.add("insufficient_improvement")
    return reasons


def _artifact_revision(artifact: LearningEvidenceArtifact) -> str:
    """重建 artifact 的 canonical 内容哈希，用于检测不可变记录遭到篡改。"""

    payload = asdict(artifact)
    payload.pop("evidence_revision")
    return _canonical_hash(payload)


def _canonical_hash(payload: object) -> str:
    """用排序 JSON 序列化匿名标量并返回稳定 SHA-256 十六进制值。"""

    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=canonical_evidence_json_value,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_hash(value: object, *, namespace: str) -> str:
    """保留规范 SHA-256；兼容内部旧测试标签时先做域分离哈希。"""
    if evidence_sha256(value):
        return str(value)
    return _canonical_hash({"namespace": namespace, "value": str(value)})


def _valid_config_snapshot(snapshot: object) -> bool:
    """验证配置快照 revision 及两个有限归一化生产权重。"""
    if not isinstance(snapshot, FeedbackRankingConfigSnapshot) or not evidence_sha256(
        snapshot.source_config_revision
    ):
        return False
    weights = (snapshot.document_route_weight, snapshot.graph_route_weight)
    return all(_finite(item, minimum=0.0) and item <= 1.0 for item in weights) and (
        math.isclose(sum(weights), 1.0, abs_tol=1e-12)
    )


def _evaluator_config_from_record(value: object) -> EvidenceEvaluatorConfig | None:
    """从状态记录严格恢复 evaluator config，拒绝未知字段。"""

    if not isinstance(value, Mapping) or set(value) != _EVALUATOR_CONFIG_FIELDS:
        return None
    stage_names = value.get("retrieval_stage_names")
    if not isinstance(stage_names, (list, tuple)):
        return None
    try:
        payload = dict(value)
        payload["retrieval_stage_names"] = tuple(stage_names)
        config = EvidenceEvaluatorConfig(**payload)
    except (TypeError, ValueError):
        return None
    return config if _valid_evaluator_config(config) else None


def _valid_evaluator_config(config: object) -> bool:
    """验证评测配置类型、范围与低敏 stage 名称。"""

    if not isinstance(config, EvidenceEvaluatorConfig):
        return False
    ratios = (
        config.max_latency_regression_ratio,
        config.max_provider_cost_regression_ratio,
        config.max_token_cost_regression_ratio,
        config.minimum_improvement_ratio,
    )
    stage_names = config.retrieval_stage_names
    return (
        positive_evidence_int(config.bootstrap_iterations)
        and config.bootstrap_iterations <= 100_000
        and _finite(config.confidence_level)
        and 0.0 < config.confidence_level < 1.0
        and all(_finite(item, minimum=0.0) and item <= 1.0 for item in ratios)
        and isinstance(stage_names, tuple)
        and 0 < len(stage_names) <= 32
        and len(stage_names) == len(set(stage_names))
        and all(safe_evidence_stage_name(item) for item in stage_names)
    )


def _metric_records(
    value: object,
    *,
    fields: frozenset[str],
    factory: type[QualityMetricEvidence] | type[LatencyEvidence],
) -> list[QualityMetricEvidence] | list[LatencyEvidence] | None:
    """严格恢复同一种 metric 记录，不接受未知字段或隐式类型转换。"""

    if not isinstance(value, (list, tuple)):
        return None
    restored: list[Any] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            return None
        try:
            restored.append(factory(**dict(item)))
        except (TypeError, ValueError):
            return None
    return restored


def _finite(value: object, *, minimum: float | None = None) -> bool:
    """验证普通有限数值，并可选要求不低于给定下界。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (minimum is None or numeric >= minimum)


def _valid_latency(metric: LatencyEvidence) -> bool:
    """确认延迟证据四个数值均有效且基线 p95 为正。"""
    return (
        _finite(metric.baseline_p50_ms, minimum=0.0)
        and _finite(metric.baseline_p95_ms, minimum=0.0)
        and _finite(metric.candidate_p50_ms, minimum=0.0)
        and _finite(metric.candidate_p95_ms, minimum=0.0)
        and metric.baseline_p95_ms > 0.0
    )


def _regressed(baseline: float, candidate: float, maximum_ratio: float) -> bool:
    """判断候选相对基线是否越过允许的恶化比例，覆盖零基线。"""
    if baseline == 0.0:
        return candidate > 0.0
    return candidate > baseline * (1.0 + maximum_ratio)


def _cost_reasons(
    reasons: set[str],
    *,
    baseline: float | None,
    candidate: float | None,
    maximum_regression: float,
    missing_reason: str,
    regression_reason: str,
) -> None:
    """把 provider/token 成本缺失或相对回退写入稳定原因集合。"""

    if not _finite(baseline, minimum=0.0) or not _finite(candidate, minimum=0.0):
        reasons.add(missing_reason)
        return
    if _regressed(float(baseline), float(candidate), maximum_regression):
        reasons.add(regression_reason)


def _has_minimum_improvement(
    quality_by_name: dict[str, QualityMetricEvidence],
    latency_by_name: dict[str, LatencyEvidence],
    artifact: LearningEvidenceArtifact,
) -> bool:
    """判断质量、p95 延迟或 token 成本是否至少有一项相对改善百分之五。"""

    improvement = artifact.evaluator_config.minimum_improvement_ratio
    for metric in quality_by_name.values():
        if metric.baseline > 0.0 and metric.candidate >= metric.baseline * (
            1.0 + improvement
        ):
            return True
    for metric in latency_by_name.values():
        if (
            metric.baseline_p95_ms > 0.0
            and metric.candidate_p95_ms <= metric.baseline_p95_ms * (1.0 - improvement)
        ):
            return True
    return bool(
        artifact.baseline_token_cost
        and artifact.candidate_token_cost is not None
        and artifact.candidate_token_cost
        <= artifact.baseline_token_cost * (1.0 - improvement)
    )
