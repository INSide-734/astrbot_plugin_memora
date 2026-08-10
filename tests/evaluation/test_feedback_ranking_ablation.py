"""反馈排序 shadow 消融的质量、漂移和隐私契约。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from core.evaluation.feedback_learning_pipeline import (
    run_feedback_ranking_evaluation_and_publish_evidence,
)
from core.evaluation.feedback_ranking_ablation import (
    FeedbackRankingConfigSnapshot,
    FeedbackRankingEvidenceRequest,
    FeedbackRankingPairedSample,
    build_feedback_ranking_replay_manifest,
    feedback_ranking_case_hash,
    run_feedback_ranking_ablation,
)
from core.evaluation.retrieval_quality import EvaluationCase
from core.features.learning.domain.feedback_learning_evidence import (
    EvidenceEvaluatorConfig,
    validate_learning_evidence,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)
from core.features.learning.infrastructure.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
)
from core.models.feedback_signal import FeedbackSignalAggregate, FeedbackSignalPolicy


def _aggregate(status: str = "candidate") -> FeedbackSignalAggregate:
    """构造有界 shadow 聚合。"""

    return FeedbackSignalAggregate(
        scope_domain="scope-synthetic",
        persona_domain=None,
        window_start=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 21, 11, tzinfo=timezone.utc),
        accepted_count=8,
        independent_window_count=2,
        decayed_support=0.9,
        proposed_document_weight=0.8,
        proposed_graph_weight=0.2,
        delta_from_baseline=0.1,
        status=status,
        policy_version=1,
    )


def _case() -> EvaluationCase:
    """构造文档路应胜出的匿名用例。"""

    return EvaluationCase(
        case_id="feedback-case-synthetic",
        query="匿名合成查询",
        relevant_doc_ids={"mem-document"},
        metadata={
            "group_label": "group-a",
            "scope_domain": "scope-synthetic",
            "persona_domain": None,
            "annotated_baseline_latency_ms": 4.0,
            "annotated_shadow_latency_ms": 4.1,
        },
    )


def _evidence_cases(count: int = 8) -> list[EvaluationCase]:
    """构造可证明 shadow 质量改善的匿名问题集合。"""

    return [
        EvaluationCase(
            case_id=f"feedback-evidence-case-{index}",
            query=f"QUERY-SECRET-CANARY-{index}",
            relevant_doc_ids={f"MEMORY-ID-CANARY-{index}"},
            metadata={
                "group_label": f"group-{index % 2}",
                "scope_domain": "scope-synthetic",
                "persona_domain": None,
            },
        )
        for index in range(count)
    ]


def _evidence_request(
    cases: list[EvaluationCase],
    *,
    regression_failures: tuple[str, ...] = (),
) -> FeedbackRankingEvidenceRequest:
    """为同一问题集合构造完整的 baseline/shadow 配对观测。"""

    start = datetime(2026, 7, 21, 10, tzinfo=timezone.utc)
    samples = tuple(
        FeedbackRankingPairedSample(
            case_hash=feedback_ranking_case_hash(case.case_id),
            observed_at_utc=(start + timedelta(minutes=index)).isoformat(),
            baseline_stage_latencies_ms=(
                ("candidate_generation", 120.0 + index),
                ("rerank", 80.0 + index),
            ),
            shadow_stage_latencies_ms=(
                ("candidate_generation", 108.0 + index),
                ("rerank", 72.0 + index),
            ),
            baseline_ttft_ms=400.0 + index,
            shadow_ttft_ms=375.0 + index,
            baseline_provider_calls=2.0,
            shadow_provider_calls=2.0,
            baseline_token_cost=100.0 + index,
            shadow_token_cost=93.0 + index,
        )
        for index, case in enumerate(cases)
    )
    return FeedbackRankingEvidenceRequest(
        aggregation_revision="a" * 64,
        quality_gate_version="quality-gate-v1",
        replay_manifest=build_feedback_ranking_replay_manifest(
            cases,
            dataset_version="feedback-ranking-fixture-v1",
        ),
        baseline_snapshot=FeedbackRankingConfigSnapshot(
            source_config_revision="b" * 64,
            document_route_weight=0.7,
            graph_route_weight=0.3,
        ),
        target_snapshot=FeedbackRankingConfigSnapshot(
            source_config_revision="b" * 64,
            document_route_weight=0.8,
            graph_route_weight=0.2,
        ),
        evaluator_config=EvidenceEvaluatorConfig(
            retrieval_stage_names=("candidate_generation", "rerank"),
        ),
        independent_window_count=2,
        paired_samples=samples,
        regression_checks=tuple(sorted(REQUIRED_EVIDENCE_REGRESSION_CHECKS)),
        regression_failures=regression_failures,
    )


async def _improving_baseline(
    case: EvaluationCase,
    _k: int,
) -> list[dict[str, Any]]:
    """返回图路噪声略高、shadow 调权后文档路相关项胜出的候选。"""

    relevant_id = next(iter(case.relevant_doc_ids))
    return [
        {"doc_id": f"noise-{case.case_id}", "score": 0.8, "route": "graph"},
        {"doc_id": relevant_id, "score": 0.75, "route": "document"},
    ]


@pytest.mark.asyncio
async def test_candidate_shadow_reweights_routes_without_online_cost() -> None:
    """candidate 只在内存中改变 route 排序并保持 Provider 成本为零。"""

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回图路噪声略高于文档路相关候选的 baseline。"""

        return [
            {"doc_id": "mem-graph-noise", "score": 0.8, "route": "graph"},
            {"doc_id": "mem-document", "score": 0.75, "route": "document"},
        ]

    report = await run_feedback_ranking_ablation(
        [_case()],
        baseline,
        _aggregate(),
        k=1,
    )

    assert report.status == "completed"
    assert report.reason_code == "accepted"
    assert report.baseline.recall_at_k == 0.0
    assert report.shadow.recall_at_k == 1.0
    assert report.shadow.observed_provider_calls is None
    assert report.shadow.observed_token_cost is None
    assert report.shadow.annotated_p50_latency_ms == 4.1
    assert report.weight_delta == 0.1
    assert report.attack_drift <= 0.1


@pytest.mark.asyncio
async def test_insufficient_signal_and_prerequisite_keep_baseline() -> None:
    """证据不足或前置不满足时 shadow 必须与 baseline 等价。"""

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回固定候选顺序。"""

        return [{"doc_id": "mem-document", "score": 0.7, "route": "document"}]

    retained = await run_feedback_ranking_ablation(
        [_case()],
        baseline,
        _aggregate("baseline_retained"),
        k=1,
    )
    unmet = await run_feedback_ranking_ablation(
        [_case()],
        baseline,
        _aggregate(),
        k=1,
        prerequisite_met=False,
    )

    assert retained.status == "skipped"
    assert retained.reason_code == "baseline_retained"
    assert retained.shadow == retained.baseline
    assert unmet.reason_code == "evaluation_prerequisite_unmet"
    assert unmet.shadow == unmet.baseline


@pytest.mark.asyncio
async def test_report_omits_query_ids_domains_and_canary_text() -> None:
    """shadow 报告不得泄露 query、文档 ID、domain 或异常原文。"""

    case = _case()
    case.query = "QUERY-SECRET-CANARY"
    case.relevant_doc_ids = {"MEMORY-ID-CANARY"}

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回仅用于内存评分的 canary 候选。"""

        return [{"doc_id": "MEMORY-ID-CANARY", "score": 0.8, "route": "document"}]

    report = await run_feedback_ranking_ablation(
        [case],
        baseline,
        _aggregate(),
        k=1,
    )
    serialized = json.dumps(asdict(report), ensure_ascii=False)

    for canary in (
        "QUERY-SECRET-CANARY",
        "MEMORY-ID-CANARY",
        "scope-synthetic",
    ):
        assert canary not in serialized


@pytest.mark.asyncio
async def test_cancellation_propagates() -> None:
    """baseline 取消必须传播，不能生成普通报告。"""

    async def cancelled(_case: EvaluationCase, _k: int) -> list[Any]:
        """模拟评测取消。"""

        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_feedback_ranking_ablation(
            [_case()],
            cancelled,
            _aggregate(),
            k=1,
        )


def test_policy_delta_matches_aggregate_bounds() -> None:
    """shadow 使用的 aggregate delta 不得超过固定策略上限。"""

    policy = FeedbackSignalPolicy(max_weight_delta=0.1)
    aggregate = _aggregate()

    assert abs(aggregate.delta_from_baseline) <= policy.max_weight_delta


@pytest.mark.asyncio
async def test_shadow_rejects_cross_domain_aggregate() -> None:
    """一个 scope 的 aggregate 不能影响另一个 scope 的评测用例。"""

    case = _case()
    case.metadata["scope_domain"] = "scope-other"

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """返回固定文档候选。"""

        return [{"doc_id": "mem-document", "score": 0.7, "route": "document"}]

    report = await run_feedback_ranking_ablation(
        [case],
        baseline,
        _aggregate(),
        k=1,
    )

    assert report.status == "skipped"
    assert report.reason_code == "scope_mismatch"
    assert report.shadow == report.baseline


@pytest.mark.asyncio
async def test_complete_paired_replay_builds_deterministic_evidence(tmp_path) -> None:
    """完整问题级配对样本必须生成顺序稳定且通过 Gate 的 artifact。"""

    cases = _evidence_cases()
    request = _evidence_request(cases)

    first = await run_feedback_ranking_evaluation_and_publish_evidence(
        tmp_path,
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=request,
    )
    reordered_request = replace(
        request,
        paired_samples=tuple(reversed(request.paired_samples)),
    )
    second = await run_feedback_ranking_ablation(
        list(reversed(cases)),
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=reordered_request,
    )

    assert first.evidence_status == "ready"
    assert first.evidence_reason_codes == ()
    assert first.evidence_artifact is not None
    assert first.evidence_artifact == second.evidence_artifact
    assert {item.name for item in first.evidence_artifact.latency_metrics} == {
        "candidate_generation",
        "rerank",
        "ttft",
    }
    latency_by_name = {
        item.name: item for item in first.evidence_artifact.latency_metrics
    }
    assert latency_by_name["candidate_generation"].baseline_p95_ms == 126.65
    assert latency_by_name["candidate_generation"].candidate_p95_ms == 114.65
    assert latency_by_name["rerank"].baseline_p95_ms == 86.65
    assert latency_by_name["rerank"].candidate_p95_ms == 78.65
    assert latency_by_name["ttft"].baseline_p95_ms == 406.65
    assert latency_by_name["ttft"].candidate_p95_ms == 381.65
    assert first.evidence_artifact.evaluation_k == 1
    assert len(first.evidence_artifact.dataset_hash) == 64
    assert len(first.evidence_artifact.replay_window_hash) == 64
    assert len(first.evidence_artifact.baseline_snapshot_hash) == 64
    assert len(first.evidence_artifact.target_snapshot_hash) == 64
    assert first.evidence_artifact.baseline_provider_calls == 2.0
    assert first.evidence_artifact.candidate_provider_calls == 2.0
    assert first.evidence_artifact.baseline_token_cost == 103.5
    assert first.evidence_artifact.candidate_token_cost == 96.5
    assert first.evidence_artifact.passed is True
    assert (
        await FeedbackLearningEvidenceInbox(tmp_path).load_current(
            aggregation_revision=request.aggregation_revision,
            source_config_revision=request.baseline_snapshot.source_config_revision,
            quality_gate_version=request.quality_gate_version,
        )
        == first.evidence_artifact
    )
    assert all(
        metric.ci_low > 0.0 for metric in first.evidence_artifact.quality_metrics
    )
    gate = validate_learning_evidence(
        first.evidence_artifact,
        aggregation_revision=request.aggregation_revision,
        source_config_revision=request.baseline_snapshot.source_config_revision,
        quality_gate_version=request.quality_gate_version,
    )
    assert gate.passed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field",
    [
        None,
        "baseline_stage_latencies_ms",
        "shadow_stage_latencies_ms",
        "baseline_ttft_ms",
        "shadow_ttft_ms",
        "baseline_provider_calls",
        "shadow_provider_calls",
        "baseline_token_cost",
        "shadow_token_cost",
    ],
)
async def test_missing_paired_dimension_never_builds_evidence(
    missing_field: str | None,
) -> None:
    """任一问题或性能成本维度缺失时都必须 fail-closed。"""

    cases = _evidence_cases()
    request = _evidence_request(cases)
    if missing_field is None:
        request = replace(request, paired_samples=request.paired_samples[:-1])
        expected_reason = "evidence_sample_set_mismatch"
    else:
        sample = request.paired_samples[0]
        incomplete = replace(sample, **{missing_field: None})
        request = replace(
            request,
            paired_samples=(incomplete, *request.paired_samples[1:]),
        )
        expected_reason = "evidence_sample_invalid"

    report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=request,
    )

    assert report.status == "completed"
    assert report.evidence_status == "unavailable"
    assert report.evidence_reason_codes == (expected_reason,)
    assert report.evidence_artifact is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"independent_window_count": 3}, "evidence_window_count_mismatch"),
    ],
)
async def test_replay_binding_or_window_mismatch_never_builds_evidence(
    overrides: dict[str, object],
    expected_reason: str,
) -> None:
    """manifest 与真实用例不一致或独立窗口漂移时不得生成 artifact。"""

    cases = _evidence_cases()
    request = replace(_evidence_request(cases), **overrides)

    report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=request,
    )

    assert report.evidence_status == "unavailable"
    assert report.evidence_reason_codes == (expected_reason,)
    assert report.evidence_artifact is None


@pytest.mark.asyncio
async def test_manifest_is_recomputed_from_actual_cases() -> None:
    """调用方不能用自报 dataset/replay 摘要替代真实用例 manifest。"""

    cases = _evidence_cases()
    request = _evidence_request(cases)
    tampered_manifest = replace(
        request.replay_manifest,
        case_fingerprints=request.replay_manifest.case_fingerprints[:-1],
    )

    report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=replace(request, replay_manifest=tampered_manifest),
    )

    assert report.evidence_status == "unavailable"
    assert report.evidence_reason_codes == ("evidence_manifest_mismatch",)
    assert report.evidence_artifact is None
    assert "dataset_hash" not in FeedbackRankingEvidenceRequest.__dataclass_fields__
    assert (
        "replay_window_hash" not in FeedbackRankingEvidenceRequest.__dataclass_fields__
    )


@pytest.mark.asyncio
async def test_dataset_and_actual_sample_window_change_revision() -> None:
    """真实用例或样本 UTC 窗口变化必须轮换对应 hash 与 evidence revision。"""

    first_cases = _evidence_cases()
    first_request = _evidence_request(first_cases)
    first = await run_feedback_ranking_ablation(
        first_cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=first_request,
    )

    changed_cases = _evidence_cases()
    changed_cases[0].query = "CHANGED-QUERY-SECRET-CANARY"
    changed_dataset = await run_feedback_ranking_ablation(
        changed_cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=_evidence_request(changed_cases),
    )
    shifted_samples = tuple(
        replace(
            sample,
            observed_at_utc=(
                datetime.fromisoformat(sample.observed_at_utc) + timedelta(hours=1)
            ).isoformat(),
        )
        for sample in first_request.paired_samples
    )
    changed_window = await run_feedback_ranking_ablation(
        first_cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=replace(first_request, paired_samples=shifted_samples),
    )

    assert first.evidence_artifact is not None
    assert changed_dataset.evidence_artifact is not None
    assert changed_window.evidence_artifact is not None
    assert (
        changed_dataset.evidence_artifact.dataset_hash
        != first.evidence_artifact.dataset_hash
    )
    assert (
        changed_window.evidence_artifact.replay_window_hash
        != first.evidence_artifact.replay_window_hash
    )
    assert (
        len(
            {
                first.evidence_artifact.evidence_revision,
                changed_dataset.evidence_artifact.evidence_revision,
                changed_window.evidence_artifact.evidence_revision,
            }
        )
        == 3
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_name", ["baseline_snapshot", "target_snapshot"])
async def test_config_hashes_are_recomputed_from_actual_shadow_weights(
    snapshot_name: str,
) -> None:
    """baseline/target 快照必须与本次实际策略一致，不能由调用方自证。"""

    cases = _evidence_cases()
    request = _evidence_request(cases)
    snapshot = getattr(request, snapshot_name)
    request = replace(
        request,
        **{
            snapshot_name: replace(
                snapshot,
                document_route_weight=snapshot.document_route_weight - 0.01,
            )
        },
    )

    report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=request,
    )

    assert report.evidence_status == "unavailable"
    assert report.evidence_reason_codes == ("evidence_config_snapshot_mismatch",)


@pytest.mark.asyncio
async def test_caller_cannot_weaken_evaluator_gate() -> None:
    """固定 evaluator 版本不能接受调用方放宽延迟或成本阈值。"""

    cases = _evidence_cases()
    request = _evidence_request(cases)
    weakened = replace(
        request.evaluator_config,
        max_latency_regression_ratio=0.90,
    )

    report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=replace(request, evaluator_config=weakened),
    )

    assert report.evidence_status == "unavailable"
    assert report.evidence_reason_codes == ("evidence_evaluator_config_invalid",)

    unsafe_stage = replace(
        request.evaluator_config,
        retrieval_stage_names=("SECRET-STAGE-CANARY",),
    )
    unsafe_report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=replace(request, evaluator_config=unsafe_stage),
    )
    assert unsafe_report.evidence_reason_codes == ("evidence_evaluator_config_invalid",)
    assert "SECRET-STAGE-CANARY" not in json.dumps(asdict(unsafe_report))


@pytest.mark.asyncio
async def test_mrr_uses_same_k_depth_as_recall_and_ndcg() -> None:
    """MRR 不得越过 K 读取 baseline 的更深候选。"""

    case = _case()

    async def baseline(_case: EvaluationCase, _k: int) -> list[dict[str, Any]]:
        """把相关项放在 K 之外。"""

        return [
            {"doc_id": "noise", "score": 1.0, "route": "document"},
            {"doc_id": "mem-document", "score": 0.9, "route": "document"},
        ]

    report = await run_feedback_ranking_ablation(
        [case],
        baseline,
        None,
        k=1,
    )

    assert report.baseline.recall_at_k == 0.0
    assert report.baseline.mrr == 0.0
    assert report.baseline.ndcg_at_k == 0.0


@pytest.mark.asyncio
async def test_rejected_evidence_report_contains_only_safe_reasons_and_hashes() -> None:
    """回归 artifact 可供审计，但报告不得泄露问题、文档、domain 或任意原文。"""

    cases = _evidence_cases()
    request = _evidence_request(
        cases,
        regression_failures=("privacy_regression",),
    )

    report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=request,
    )
    serialized = json.dumps(asdict(report), ensure_ascii=False)

    assert report.evidence_status == "rejected"
    assert report.evidence_artifact is not None
    assert report.evidence_reason_codes == ("regression_failures_present",)
    for canary in (
        "QUERY-SECRET-CANARY",
        "MEMORY-ID-CANARY",
        "feedback-evidence-case",
        "scope-synthetic",
    ):
        assert canary not in serialized

    unsafe_request = replace(
        request,
        regression_failures=("UNTRUSTED-REGRESSION-CANARY",),
    )
    unsafe_report = await run_feedback_ranking_ablation(
        cases,
        _improving_baseline,
        _aggregate(),
        k=1,
        evidence_request=unsafe_request,
    )
    unsafe_serialized = json.dumps(asdict(unsafe_report), ensure_ascii=False)
    assert unsafe_report.evidence_artifact is None
    assert unsafe_report.evidence_reason_codes == (
        "evidence_regression_reason_invalid",
    )
    assert "UNTRUSTED-REGRESSION-CANARY" not in unsafe_serialized
