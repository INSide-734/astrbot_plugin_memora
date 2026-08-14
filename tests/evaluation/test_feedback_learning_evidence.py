"""自主学习离线质量证据门的契约测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.features.learning.domain.feedback_learning_evidence import (
    EvidenceEvaluatorConfig,
    LatencyEvidence,
    QualityMetricEvidence,
    artifact_from_record,
    artifact_to_record,
    build_learning_evidence,
    validate_learning_evidence,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)

_AGGREGATION_REVISION = "a" * 64
_SOURCE_CONFIG_REVISION = "b" * 64
_OTHER_AGGREGATION_REVISION = "c" * 64


def _artifact(**overrides):
    """构造满足默认质量门的匿名离线证据。"""

    values = {
        "aggregation_revision": _AGGREGATION_REVISION,
        "source_config_revision": _SOURCE_CONFIG_REVISION,
        "quality_gate_version": "quality-gate-v1",
        "dataset_hash": "dataset-sha256",
        "replay_window_hash": "window-sha256",
        "evaluator_version": "evaluator-v1",
        "sample_count": 100,
        "independent_window_count": 3,
        "quality_metrics": (
            QualityMetricEvidence("Recall@K", 0.70, 0.75, 0.01, 0.09),
            QualityMetricEvidence("MRR", 0.60, 0.63, 0.00, 0.06),
            QualityMetricEvidence("nDCG", 0.65, 0.68, 0.00, 0.06),
        ),
        "latency_metrics": (
            LatencyEvidence("retrieval_stage", 100.0, 200.0, 90.0, 190.0),
            LatencyEvidence("ttft", 300.0, 400.0, 290.0, 390.0),
        ),
        "evaluation_k": 5,
        "evaluator_config": EvidenceEvaluatorConfig(),
        "baseline_snapshot_hash": "a" * 64,
        "target_snapshot_hash": "b" * 64,
        "baseline_provider_calls": 10.0,
        "candidate_provider_calls": 9.0,
        "baseline_token_cost": 10.0,
        "candidate_token_cost": 9.0,
        "regression_checks": tuple(sorted(REQUIRED_EVIDENCE_REGRESSION_CHECKS)),
        "regression_failures": (),
    }
    values.update(overrides)
    return build_learning_evidence(**values)


def test_builds_immutable_artifact_with_canonical_revision() -> None:
    """相同匿名输入必须得到稳定 revision，且冻结 artifact 不可修改。"""

    artifact = _artifact()

    assert len(artifact.evidence_revision) == 64
    assert artifact == _artifact()
    assert artifact.passed is True
    try:
        artifact.sample_count = 99
    except AttributeError:
        pass
    else:
        raise AssertionError("artifact 必须冻结")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aggregation_revision", "aggregate-raw-text"),
        ("source_config_revision", "config-raw-text"),
        ("quality_gate_version", "custom-gate-version"),
    ],
)
def test_rejects_untrusted_binding_text_before_artifact_creation(
    field: str,
    value: str,
) -> None:
    """revision 原文或未登记 Gate 版本不得进入可序列化 artifact。"""

    with pytest.raises(ValueError, match="learning_evidence_binding_invalid"):
        _artifact(**{field: value})


def test_accepts_complete_passing_evidence() -> None:
    """完整、绑定一致且具有至少一项改善的证据必须通过。"""

    artifact = _artifact()

    result = validate_learning_evidence(
        artifact,
        aggregation_revision=_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is True
    assert result.reason_codes == ()


def test_empty_regression_check_set_cannot_mean_all_checks_passed() -> None:
    """未执行必需回归套件时，即使 failure 为空也不得通过质量门。"""

    artifact = _artifact(regression_checks=())
    result = validate_learning_evidence(
        artifact,
        aggregation_revision=_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is False
    assert "required_regression_checks_missing" in result.reason_codes


def test_rejects_mutated_or_mismatched_bindings() -> None:
    """revision 校验和任一关键绑定漂移都必须拒绝 evidence。"""

    artifact = _artifact()
    mutated = replace(artifact, sample_count=101)

    result = validate_learning_evidence(
        mutated,
        aggregation_revision=_OTHER_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is False
    assert "evidence_revision_mismatch" in result.reason_codes
    assert "aggregation_revision_mismatch" in result.reason_codes


def test_rejects_missing_required_quality_latency_cost_or_metadata() -> None:
    """缺少质量项、stage/TTFT、成本或样本版本信息均不可发布。"""

    artifact = _artifact(
        quality_metrics=(
            QualityMetricEvidence("Recall@K", 0.7, 0.75, 0.01, 0.09),
            QualityMetricEvidence("MRR", 0.6, 0.63, 0.0, 0.06),
        ),
        latency_metrics=(LatencyEvidence("retrieval_stage", 100, 200, 90, 190),),
        baseline_provider_calls=None,
        baseline_token_cost=None,
        baseline_snapshot_hash="",
        sample_count=0,
        independent_window_count=0,
        evaluation_k=0,
        evaluator_version="",
    )

    result = validate_learning_evidence(
        artifact,
        aggregation_revision=_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is False
    assert {
        "missing_quality_metric",
        "missing_latency_metric",
        "missing_provider_cost",
        "missing_token_cost",
        "invalid_snapshot_hash",
        "invalid_sample_count",
        "invalid_window_count",
        "invalid_evaluation_k",
        "missing_evaluator_version",
    }.issubset(result.reason_codes)


def test_rejects_quality_ci_latency_cost_and_regression_failures() -> None:
    """质量下界、性能回退、成本回退和领域回归必须独立阻断。"""

    artifact = _artifact(
        quality_metrics=(
            QualityMetricEvidence("Recall@K", 0.70, 0.75, -0.01, 0.09),
            QualityMetricEvidence("MRR", 0.60, 0.63, 0.00, 0.06),
            QualityMetricEvidence("nDCG", 0.65, 0.68, 0.00, 0.06),
        ),
        latency_metrics=(
            LatencyEvidence("retrieval_stage", 100, 200, 111, 221),
            LatencyEvidence("ttft", 300, 400, 300, 401),
        ),
        baseline_provider_calls=10.0,
        candidate_provider_calls=10.6,
        baseline_token_cost=10.0,
        candidate_token_cost=10.6,
        regression_failures=("privacy",),
    )

    result = validate_learning_evidence(
        artifact,
        aggregation_revision=_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is False
    assert "quality_ci_regression" in result.reason_codes
    assert "latency_p50_regression" in result.reason_codes
    assert "latency_p95_regression" in result.reason_codes
    assert "provider_cost_regression" in result.reason_codes
    assert "token_cost_regression" in result.reason_codes
    assert "regression_failures_present" in result.reason_codes


def test_requires_a_five_percent_improvement() -> None:
    """全部非回退但没有任何五个百分点相对改善时仍不可发布。"""

    artifact = _artifact(
        quality_metrics=(
            QualityMetricEvidence("Recall@K", 0.70, 0.71, 0.00, 0.02),
            QualityMetricEvidence("MRR", 0.60, 0.61, 0.00, 0.02),
            QualityMetricEvidence("nDCG", 0.65, 0.66, 0.00, 0.02),
        ),
        latency_metrics=(
            LatencyEvidence("retrieval_stage", 100, 200, 100, 200),
            LatencyEvidence("ttft", 300, 400, 300, 400),
        ),
        baseline_token_cost=10.0,
        candidate_token_cost=10.0,
    )

    result = validate_learning_evidence(
        artifact,
        aggregation_revision=_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is False
    assert result.reason_codes == ("insufficient_improvement",)


def test_revision_covers_snapshots_k_evaluator_provider_and_all_stages() -> None:
    """所有生产判定输入都必须进入 canonical revision 并可严格恢复。"""

    config = EvidenceEvaluatorConfig(
        retrieval_stage_names=("candidate_generation", "rerank"),
    )
    latency = (
        LatencyEvidence("candidate_generation", 30, 50, 28, 47),
        LatencyEvidence("rerank", 20, 40, 18, 37),
        LatencyEvidence("ttft", 300, 400, 280, 370),
    )
    artifact = _artifact(evaluator_config=config, latency_metrics=latency)

    variants = (
        _artifact(
            evaluator_config=config,
            latency_metrics=latency,
            baseline_snapshot_hash="c" * 64,
        ),
        _artifact(
            evaluator_config=config,
            latency_metrics=latency,
            target_snapshot_hash="d" * 64,
        ),
        _artifact(evaluator_config=config, latency_metrics=latency, evaluation_k=6),
        _artifact(
            evaluator_config=replace(config, bootstrap_iterations=1_000),
            latency_metrics=latency,
        ),
        _artifact(
            evaluator_config=config,
            latency_metrics=latency,
            candidate_provider_calls=8.0,
        ),
        _artifact(
            evaluator_config=config,
            latency_metrics=(
                latency[0],
                replace(latency[1], candidate_p50_ms=17.0),
                latency[2],
            ),
        ),
    )

    assert all(
        item.evidence_revision != artifact.evidence_revision for item in variants
    )
    assert artifact_from_record(artifact_to_record(artifact)) == artifact


def test_declared_passed_must_match_recomputed_gate() -> None:
    """artifact 的 passed 既参与哈希，也不能与重新计算的 Gate 分叉。"""

    artifact = _artifact()
    tampered = replace(artifact, passed=False)

    result = validate_learning_evidence(
        tampered,
        aggregation_revision=_AGGREGATION_REVISION,
        source_config_revision=_SOURCE_CONFIG_REVISION,
        quality_gate_version="quality-gate-v1",
    )

    assert result.passed is False
    assert "evidence_revision_mismatch" in result.reason_codes
    assert "passed_mismatch" in result.reason_codes
