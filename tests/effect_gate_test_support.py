"""效果门 API/service 测试共用的确定性夹具。"""

from __future__ import annotations

from typing import Any

from core.features.evaluation.application.retrieval_quality import (
    EvaluationReport,
    EvaluationResult,
)

_REQUIRED_CATEGORIES = (
    "positive",
    "negative",
    "ambiguous",
    "cross_scope",
    "relation",
    "multi_hop",
    "long_payload",
)
_METRICS = (
    "final_injected_hit_rate",
    "injection_precision",
    "required_fact_coverage",
    "negative_injection_rate",
    "observed_latency_ms",
    "observed_provider_calls",
)
_HASH = "a" * 64


def _metric(baseline: float, variant: float) -> dict[str, Any]:
    return {
        "available": True,
        "pairs": 10,
        "baseline": baseline,
        "variant": variant,
        "delta": round(variant - baseline, 4),
        "ci_low": 0.01,
        "ci_high": 0.2,
        "margin": 0.02,
    }


def _factors() -> dict[str, Any]:
    factors = {
        "atom": ("atom_signal_on", "pass_enable", "enable", ["pareto_improvement"]),
        "graph_route": (
            "graph_route_off",
            "pass_disable",
            "degrade_to_document_only",
            ["graph_removal_non_inferior"],
        ),
        "dynamic_weights": (
            "dynamic_route_weighting_on",
            "reject",
            "keep_disabled",
            ["guardrail_regression"],
        ),
        "rerank": (
            "final_reranker_mmr",
            "insufficient_evidence",
            "collect_more_evidence",
            ["missing_primary_metric"],
        ),
    }
    payload: dict[str, Any] = {}
    for factor, (variant, status, recommendation, reasons) in factors.items():
        metrics = (
            {}
            if status in {"insufficient_evidence", "not_applicable"}
            else {name: _metric(0.5, 0.6) for name in _METRICS}
        )
        categories = (
            {}
            if not metrics
            else {
                "positive": {
                    "case_count": 10,
                    "final_injected_hit_rate": {
                        "baseline": 0.5,
                        "variant": 0.6,
                        "delta": 0.1,
                    },
                },
                "negative": {
                    "case_count": 10,
                    "negative_injection_rate": {
                        "baseline": 0.1,
                        "variant": 0.05,
                        "delta": -0.05,
                    },
                },
            }
        )
        payload[factor] = {
            "factor": factor,
            "variant": variant,
            "status": status,
            "recommendation": recommendation,
            "reason_codes": reasons,
            "metrics": metrics,
            "categories": categories,
        }
    return payload


def gate_payload(**overrides: Any) -> dict[str, Any]:
    """返回可通过 allowlist 的完整 gate 报告。"""

    payload: dict[str, Any] = {
        "schema_version": "effect-gate-v1",
        "baseline": "fixed_route_baseline",
        "readiness": {
            "required_categories": list(_REQUIRED_CATEGORIES),
            "present_counts": {name: 10 for name in _REQUIRED_CATEGORIES},
            "total_cases": 70,
            "missing": [],
            "can_evaluate": True,
        },
        "factors": _factors(),
        "recommended_config": {
            "atom_route_weight": 0.25,
            "graph_retrieval_enabled": False,
        },
        "policy": {
            "policy_version": "effect-gate-v1",
            "required_categories": list(_REQUIRED_CATEGORIES),
            "min_cases_per_category": 10,
            "min_total_cases": 60,
            "confidence": 0.95,
            "bootstrap_iterations": 2000,
            "rate_margin": 0.02,
            "latency_margin_ms": 50.0,
            "latency_margin_ratio": 0.1,
            "provider_calls_margin": 0.0,
            "negative_injection_margin": 0.02,
        },
        "manifest": {
            "data_source": "plugin_evaluation_datasets",
            "code_revision": _HASH,
            "config_hash": _HASH,
            "schema_hash": _HASH,
            "fixture_hmac": _HASH,
            "db_snapshot_hash": _HASH,
            "seed": 0,
            "k": 5,
            "case_count": 70,
            "injection_config_hash": _HASH,
            "gate_policy_version": "effect-gate-v1",
            "gate_policy": {
                "policy_version": "effect-gate-v1",
                "required_categories": list(_REQUIRED_CATEGORIES),
                "min_cases_per_category": 10,
                "min_total_cases": 60,
                "confidence": 0.95,
                "bootstrap_iterations": 2000,
                "rate_margin": 0.02,
                "latency_margin_ms": 50.0,
                "latency_margin_ratio": 0.1,
                "provider_calls_margin": 0.0,
                "negative_injection_margin": 0.02,
            },
        },
        "privacy_canary": {"checked_values": 140, "violations": []},
    }
    payload.update(overrides)
    return payload


def evaluation_report(
    *,
    case_id: str = "case-1",
    query: str = "secret query",
    total_cases: int = 1,
) -> EvaluationReport:
    """返回可被 report_summary/report_cases 处理的最小报告。"""

    result = EvaluationResult(
        case_id=case_id,
        query=query,
        ranked_doc_ids=["1"],
        relevant_doc_ids={"1"},
        recall_at_k=1.0,
        reciprocal_rank=1.0,
        ndcg_at_k=1.0,
        observed_latency_ms=10.0,
        precision_at_k=1.0,
        injection={
            "final_injected_hit": True,
            "injection_precision": 1.0,
            "required_fact_coverage": 1.0,
            "negative_injection": False,
        },
    )
    return EvaluationReport(
        total_cases=total_cases,
        k=5,
        recall_at_k=1.0,
        mrr=1.0,
        ndcg_at_k=1.0,
        observed_p95_latency_ms=10.0,
        observed_provider_calls=1.0,
        cases=[result],
        dataset_breakdown={"runtime": {"case_count": total_cases}},
        injection_summary={
            "final_injected_hit_rate": 1.0,
            "injection_precision": 1.0,
            "required_fact_coverage": 1.0,
            "negative_injection_rate": 0.0,
        },
    )
