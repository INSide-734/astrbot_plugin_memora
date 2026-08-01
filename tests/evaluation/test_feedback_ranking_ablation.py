"""反馈排序 shadow 消融的质量、漂移和隐私契约。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pytest

from core.evaluation.feedback_ranking_ablation import run_feedback_ranking_ablation
from core.evaluation.retrieval_quality import EvaluationCase
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
