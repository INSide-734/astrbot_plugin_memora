"""效果门 readiness、Pareto 决策与推荐配置契约。"""

from __future__ import annotations

from dataclasses import dataclass

from core.features.evaluation.domain.effect_gate import (
    EffectGate,
    EffectGatePolicy,
    VariantEvidence,
)

_POSITIVE_CATEGORIES = (
    "positive",
    "ambiguous",
    "cross_scope",
    "relation",
    "multi_hop",
    "long_payload",
)


@dataclass
class _Case:
    case_id: str
    metadata: dict[str, object]
    injection: dict[str, object]
    observed_latency_ms: float = 10.0
    advanced_metrics: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.advanced_metrics is None:
            self.advanced_metrics = {"observed_provider_calls": 1.0}


@dataclass
class _Report:
    cases: list[_Case]


def _report(
    *,
    hit_count: int,
    precision: float | None,
    coverage: float | None,
    latency_ms: float = 10.0,
    negative_injection: bool = False,
) -> _Report:
    cases: list[_Case] = []
    for category in _POSITIVE_CATEGORIES:
        for index in range(10):
            cases.append(
                _Case(
                    case_id=f"{category}-{index}",
                    metadata={"categories": [category]},
                    injection={
                        "final_injected_hit": index < hit_count,
                        "injection_precision": precision,
                        "required_fact_coverage": coverage,
                        "negative_injection": None,
                    },
                    observed_latency_ms=latency_ms,
                )
            )
    for index in range(10):
        cases.append(
            _Case(
                case_id=f"negative-{index}",
                metadata={"categories": ["negative"]},
                injection={
                    "final_injected_hit": False,
                    "injection_precision": None,
                    "required_fact_coverage": None,
                    "negative_injection": negative_injection,
                },
                observed_latency_ms=latency_ms,
            )
        )
    return _Report(cases)


def _policy() -> EffectGatePolicy:
    return EffectGatePolicy(bootstrap_iterations=200)


def _evaluate(variant: VariantEvidence, *, baseline: _Report | None = None):
    return EffectGate().evaluate(
        baseline=VariantEvidence(
            factor="baseline",
            variant="fixed_route_baseline",
            report=baseline or _report(hit_count=5, precision=0.5, coverage=0.5),
            target_calls=1,
        ),
        variants={variant.factor: variant},
        policy=_policy(),
    )


def test_gate_enables_atom_when_primary_metrics_improve() -> None:
    result = _evaluate(
        VariantEvidence(
            factor="atom",
            variant="atom_signal_on",
            report=_report(hit_count=10, precision=1.0, coverage=1.0),
            target_calls=1,
        )
    )

    factor = result["factors"]["atom"]
    assert result["readiness"]["can_evaluate"] is True
    assert factor["status"] == "pass_enable"
    assert factor["recommendation"] == "enable"
    assert result["recommended_config"]["atom_route_weight"] == 0.25
    assert factor["metrics"]["final_injected_hit_rate"]["available"] is True


def test_gate_rejects_guardrail_regression() -> None:
    result = _evaluate(
        VariantEvidence(
            factor="atom",
            variant="atom_signal_on",
            report=_report(
                hit_count=10,
                precision=1.0,
                coverage=1.0,
                latency_ms=200.0,
            ),
            target_calls=1,
        )
    )

    factor = result["factors"]["atom"]
    assert factor["status"] == "reject"
    assert factor["recommendation"] == "keep_disabled"
    assert "guardrail_regression" in factor["reason_codes"]


def test_gate_keeps_feature_disabled_without_improvement() -> None:
    baseline = _report(hit_count=5, precision=0.5, coverage=0.5)
    result = _evaluate(
        VariantEvidence(
            factor="atom",
            variant="atom_signal_on",
            report=_report(hit_count=5, precision=0.5, coverage=0.5),
            target_calls=1,
        ),
        baseline=baseline,
    )

    factor = result["factors"]["atom"]
    assert factor["status"] == "pass_disable"
    assert factor["recommendation"] == "keep_disabled"
    assert result["recommended_config"] == {}


def test_gate_disables_graph_route_when_non_inferior() -> None:
    baseline = _report(hit_count=5, precision=0.5, coverage=0.5)
    result = _evaluate(
        VariantEvidence(
            factor="graph_route",
            variant="graph_route_off",
            report=_report(hit_count=5, precision=0.5, coverage=0.5),
            target_calls=0,
        ),
        baseline=baseline,
    )

    factor = result["factors"]["graph_route"]
    assert factor["status"] == "pass_disable"
    assert factor["recommendation"] == "degrade_to_document_only"
    assert result["recommended_config"]["graph_retrieval_enabled"] is False


def test_gate_keeps_graph_when_removal_degrades_quality() -> None:
    result = _evaluate(
        VariantEvidence(
            factor="graph_route",
            variant="graph_route_off",
            report=_report(hit_count=1, precision=0.1, coverage=0.1),
            target_calls=0,
        )
    )

    factor = result["factors"]["graph_route"]
    assert factor["status"] == "pass_enable"
    assert factor["reason_codes"] == ["graph_removal_degrades_quality"]


def test_gate_fails_closed_for_missing_categories_and_probes() -> None:
    partial = _Report(
        [
            _Case(
                case_id=f"positive-{index}",
                metadata={"categories": ["positive"]},
                injection={
                    "final_injected_hit": True,
                    "injection_precision": 1.0,
                    "required_fact_coverage": 1.0,
                    "negative_injection": None,
                },
            )
            for index in range(10)
        ]
    )
    readiness = _evaluate(
        VariantEvidence(
            factor="atom",
            variant="atom_signal_on",
            report=partial,
            target_calls=1,
        ),
        baseline=partial,
    )
    assert readiness["readiness"]["can_evaluate"] is False
    assert readiness["factors"]["atom"]["reason_codes"] == ["insufficient_categories"]

    probe = _evaluate(
        VariantEvidence(
            factor="atom",
            variant="atom_signal_on",
            report=_report(hit_count=10, precision=1.0, coverage=1.0),
            target_calls=0,
        )
    )
    assert probe["factors"]["atom"]["reason_codes"] == ["variant_not_exercised"]

    not_applicable = _evaluate(
        VariantEvidence(
            factor="atom",
            variant="atom_signal_on",
            report=None,
            status="skipped",
            reason_code="missing_atom_retriever",
        )
    )
    assert not_applicable["factors"]["atom"]["status"] == "not_applicable"
