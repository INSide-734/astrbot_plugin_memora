"""候选到最终注入的观察器、分母与类别聚合契约。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.features.evaluation.application.injection_ablation import (
    InjectionAblationObserver,
    build_category_breakdown,
)
from core.features.evaluation.application.retrieval_quality import (
    EvaluationCase,
    evaluate_cases,
)
from core.features.injection.application.router import (
    InjectionRoutingConfig,
    InjectionStrategyRouter,
)
from core.features.injection.application.selection import select_candidates
from core.features.injection.domain.models import (
    PresetName,
    RequestSignals,
    RoutingMode,
)
from tests.fact_evidence_helpers import candidate_evidence_metadata


def _decision():
    config = InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        manual_preset=PresetName.BALANCED,
    )
    return InjectionStrategyRouter().route_final(config, RequestSignals())


def _candidate(doc_id: int = 1, content: str = "事实一") -> dict[str, object]:
    return {
        "doc_id": doc_id,
        "content": content,
        "score": 0.9,
        "metadata": candidate_evidence_metadata(content, importance=0.8),
    }


def _positive_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="positive-1",
        query="事实一是什么",
        relevant_doc_ids={"1"},
        metadata={
            "categories": ["positive"],
            "required_key_facts": ["事实一"],
        },
    )


def test_observer_reports_candidate_and_final_injection() -> None:
    observer = InjectionAblationObserver(
        decision=_decision(),
        select_candidates=select_candidates,
    )
    observation = observer.observe(_positive_case(), [_candidate()])

    assert observation.candidate_count == 1
    assert observation.selected_count == 1
    assert observation.candidate_hit is True
    assert observation.final_injected_hit is True
    assert observation.injection_precision == 1.0
    assert observation.required_fact_coverage == 1.0
    assert observation.negative_injection is None
    assert observation.source_rejected_count == 0
    assert observation.selection_dropped_count == 0
    assert observation.route_signals["document_route_weight"] is None


def test_observer_negative_case_counts_injection_without_precision_leak() -> None:
    observer = InjectionAblationObserver(
        decision=_decision(),
        select_candidates=select_candidates,
    )
    case = EvaluationCase(
        case_id="negative-1",
        query="没有相关记忆",
        relevant_doc_ids={"__no_relevant__"},
        metadata={"categories": ["negative"], "expected_no_hit": True},
    )
    observation = observer.observe(case, [_candidate()])

    assert observation.negative_injection is True
    assert observation.final_injected_hit is False
    assert observation.injection_precision == 0.0
    assert observation.required_fact_coverage is None


def test_observer_separates_source_gate_and_selection_drops() -> None:
    observer = InjectionAblationObserver(
        decision=_decision(),
        select_candidates=select_candidates,
    )
    source_rejected = observer.observe(
        _positive_case(),
        [{"doc_id": 1, "content": "事实一", "score": 0.9, "metadata": {}}],
    )
    tight = InjectionAblationObserver(
        decision=replace(_decision(), memory_budget_chars=1),
        select_candidates=select_candidates,
    )
    budget_rejected = tight.observe(_positive_case(), [_candidate()])

    assert source_rejected.source_rejected_count == 1
    assert source_rejected.selected_count == 0
    assert source_rejected.selection_dropped_count == 0
    assert budget_rejected.source_rejected_count == 0
    assert budget_rejected.selected_count == 0
    assert budget_rejected.selection_dropped_count == 1
    assert budget_rejected.budget_utilization == 0.0


@pytest.mark.asyncio
async def test_evaluate_cases_aggregates_injection_and_categories() -> None:
    observer = InjectionAblationObserver(
        decision=_decision(),
        select_candidates=select_candidates,
    )
    case = _positive_case()

    async def retriever(_case: EvaluationCase, _k: int):
        return [_candidate()]

    report = await evaluate_cases(
        [case],
        retriever,
        k=5,
        case_observer=lambda item, documents: observer.observe(item, documents),
    )

    assert report.injection_summary["total_cases"] == 1
    assert report.injection_summary["final_injected_hit_rate"] == 1.0
    assert report.category_breakdown["positive"]["case_count"] == 1
    assert report.cases[0].injection is not None


def test_category_breakdown_keeps_null_denominators() -> None:
    class Result:
        metadata = {"categories": ["negative"]}
        injection = {
            "candidate_hit": False,
            "final_injected_hit": False,
            "injection_precision": None,
            "negative_injection": False,
            "required_fact_coverage": None,
            "dropped_relevant": 0,
            "budget_utilization": None,
            "source_rejected_count": 0,
            "selection_dropped_count": 0,
            "injection_simulation_ms": 0.1,
            "resolved_preset": "balanced",
            "decision_reason_codes": ["MANUAL_SELECTED"],
        }
        observed_latency_ms = 1.0
        advanced_metrics = {"observed_provider_calls": 1.0}

    breakdown = build_category_breakdown([Result()])

    assert breakdown["negative"]["case_count"] == 1
    assert breakdown["negative"]["injection_precision"] is None
    assert breakdown["negative"]["negative_injection_rate"] == 0.0
    assert breakdown["negative"]["observed_provider_calls"] == 1.0
