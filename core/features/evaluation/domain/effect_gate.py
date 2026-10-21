"""效果门的 Pareto 判定、类别 readiness 与报告 DTO。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .statistics import (
    paired_bootstrap_mean_ci,
    paired_p95_delta_bootstrap_ci,
)

__all__ = [
    "EffectGate",
    "EffectGatePolicy",
    "VariantEvidence",
]

REQUIRED_CATEGORIES: tuple[str, ...] = (
    "positive",
    "negative",
    "ambiguous",
    "cross_scope",
    "relation",
    "multi_hop",
    "long_payload",
)
PRIMARY_METRICS: tuple[str, ...] = (
    "final_injected_hit_rate",
    "injection_precision",
    "required_fact_coverage",
)
GUARDRAIL_METRICS: tuple[str, ...] = (
    "negative_injection_rate",
    "observed_latency_ms",
    "observed_provider_calls",
)
_FACTOR_CONFIG: dict[str, dict[str, Any]] = {
    "atom": {"atom_route_weight": 0.25},
    "graph_route": {"graph_retrieval_enabled": False},
    "dynamic_weights": {"dynamic_route_weighting": True},
    "rerank": {"final_reranker": "mmr"},
}
_DISABLE_FACTORS = frozenset({"graph_route"})


@dataclass(frozen=True, slots=True)
class EffectGatePolicy:
    """效果门阈值；默认值与报告中的 gate_policy_version 绑定。"""

    min_cases_per_category: int = 10
    min_total_cases: int = 60
    confidence: float = 0.95
    bootstrap_iterations: int = 2000
    rate_margin: float = 0.02
    latency_margin_ms: float = 50.0
    latency_margin_ratio: float = 0.10
    provider_calls_margin: float = 0.0
    negative_injection_margin: float = 0.02
    policy_version: str = "effect-gate-v1"

    def as_payload(self) -> dict[str, Any]:
        """返回可写入报告的阈值快照。"""

        return {
            "policy_version": self.policy_version,
            "required_categories": list(REQUIRED_CATEGORIES),
            "min_cases_per_category": self.min_cases_per_category,
            "min_total_cases": self.min_total_cases,
            "confidence": self.confidence,
            "bootstrap_iterations": self.bootstrap_iterations,
            "rate_margin": self.rate_margin,
            "latency_margin_ms": self.latency_margin_ms,
            "latency_margin_ratio": self.latency_margin_ratio,
            "provider_calls_margin": self.provider_calls_margin,
            "negative_injection_margin": self.negative_injection_margin,
        }


@dataclass(frozen=True, slots=True)
class VariantEvidence:
    """单个门禁因子的运行证据。"""

    factor: str
    variant: str
    report: Any | None
    status: str = "completed"
    reason_code: str = "available"
    target_calls: int | None = None
    target_applied: bool | None = None


class EffectGate:
    """按 Pareto 规则比较固定 baseline 与单因子变体。"""

    def evaluate(
        self,
        *,
        baseline: VariantEvidence,
        variants: Mapping[str, VariantEvidence],
        manifest: Mapping[str, Any] | None = None,
        privacy_canary: Mapping[str, Any] | None = None,
        policy: EffectGatePolicy | None = None,
    ) -> dict[str, Any]:
        """返回只包含计数、数值、闭集枚举和推荐配置的报告。"""

        active_policy = policy or EffectGatePolicy()
        readiness = _readiness(baseline.report, active_policy)
        factors: dict[str, Any] = {}
        recommended: dict[str, Any] = {}
        for factor, evidence in variants.items():
            payload = self._evaluate_factor(
                factor,
                baseline,
                evidence,
                readiness,
                active_policy,
                bonferroni_factor=max(1, len(variants) * len(PRIMARY_METRICS)),
            )
            factors[factor] = payload
            recommended.update(payload.pop("_recommended_config", {}))
        return {
            "schema_version": "effect-gate-v1",
            "baseline": baseline.variant,
            "readiness": readiness,
            "factors": factors,
            "recommended_config": recommended,
            "policy": active_policy.as_payload(),
            "manifest": dict(manifest or {}),
            "privacy_canary": dict(
                privacy_canary or {"checked_values": 0, "violations": []}
            ),
        }

    def _evaluate_factor(
        self,
        factor: str,
        baseline: VariantEvidence,
        variant: VariantEvidence,
        readiness: Mapping[str, Any],
        policy: EffectGatePolicy,
        *,
        bonferroni_factor: int,
    ) -> dict[str, Any]:
        base = {
            "factor": factor,
            "variant": variant.variant,
            "status": "insufficient_evidence",
            "recommendation": "collect_more_evidence",
            "reason_codes": [],
            "metrics": {},
            "categories": {},
            "_recommended_config": {},
        }
        if not readiness.get("can_evaluate"):
            base["reason_codes"] = ["insufficient_categories"]
            return base
        if variant.report is None or variant.status != "completed":
            base["status"] = (
                "not_applicable"
                if _is_not_applicable(variant.reason_code)
                else "insufficient_evidence"
            )
            base["reason_codes"] = [variant.reason_code or "variant_not_executed"]
            return base

        probe_reason = _probe_reason(factor, baseline, variant)
        if probe_reason is not None:
            base["reason_codes"] = [probe_reason]
            return base

        pairs = _paired_metric_values(baseline.report, variant.report)
        confidence = 1.0 - (1.0 - policy.confidence) / bonferroni_factor
        metrics = {
            name: _metric_summary(
                name,
                pairs.get(name, {}),
                policy,
                confidence=confidence,
                seed=_seed(factor, name),
            )
            for name in (*PRIMARY_METRICS, *GUARDRAIL_METRICS)
        }
        base["metrics"] = metrics
        base["categories"] = _category_metrics(baseline.report, variant.report)

        missing = [name for name in PRIMARY_METRICS if not metrics[name]["available"]]
        if missing:
            base["reason_codes"] = ["missing_primary_metric"]
            return base

        primary_non_inferior = all(
            metrics[name]["ci_low"] >= -_margin(name, metrics[name], policy)
            for name in PRIMARY_METRICS
        )
        improved = any(metrics[name]["ci_low"] > 0.0 for name in PRIMARY_METRICS)
        guardrail_ok = all(
            metrics[name]["ci_high"] <= _margin(name, metrics[name], policy)
            for name in GUARDRAIL_METRICS
        )

        if factor in _DISABLE_FACTORS:
            return _disable_decision(
                base,
                factor,
                primary_non_inferior=primary_non_inferior,
                guardrail_ok=guardrail_ok,
            )
        return _enable_decision(
            base,
            factor,
            improved=improved,
            primary_non_inferior=primary_non_inferior,
            guardrail_ok=guardrail_ok,
        )


def _readiness(report: Any | None, policy: EffectGatePolicy) -> dict[str, Any]:
    cases = list(getattr(report, "cases", ()) or ())
    counts: dict[str, int] = defaultdict(int)
    for case in cases:
        for category in _categories(getattr(case, "metadata", {}) or {}):
            counts[category] += 1
    present = {category: counts.get(category, 0) for category in REQUIRED_CATEGORIES}
    missing = [
        category
        for category in REQUIRED_CATEGORIES
        if present[category] < policy.min_cases_per_category
    ]
    can_evaluate = not missing and len(cases) >= policy.min_total_cases
    return {
        "required_categories": list(REQUIRED_CATEGORIES),
        "present_counts": present,
        "total_cases": len(cases),
        "missing": missing,
        "can_evaluate": can_evaluate,
    }


def _probe_reason(
    factor: str,
    baseline: VariantEvidence,
    variant: VariantEvidence,
) -> str | None:
    direction = "disable" if factor in _DISABLE_FACTORS else "enable"
    if direction == "enable":
        if variant.target_calls is None:
            return "execution_probe_missing"
        return None if variant.target_calls > 0 else "variant_not_exercised"
    if variant.target_calls is None:
        return "execution_probe_missing"
    if variant.target_calls != 0:
        return "variant_not_exercised"
    if baseline.target_calls is None or baseline.target_calls <= 0:
        return "baseline_component_not_exercised"
    return None


def _enable_decision(
    payload: dict[str, Any],
    factor: str,
    *,
    improved: bool,
    primary_non_inferior: bool,
    guardrail_ok: bool,
) -> dict[str, Any]:
    if not primary_non_inferior or not guardrail_ok:
        payload["status"] = "reject"
        payload["recommendation"] = "keep_disabled"
        payload["reason_codes"] = (
            ["primary_regression"]
            if not primary_non_inferior
            else ["guardrail_regression"]
        )
        return payload
    if improved:
        payload["status"] = "pass_enable"
        payload["recommendation"] = "enable"
        payload["reason_codes"] = ["pareto_improvement"]
        payload["_recommended_config"] = dict(_FACTOR_CONFIG.get(factor, {}))
        return payload
    payload["status"] = "pass_disable"
    payload["recommendation"] = "keep_disabled"
    payload["reason_codes"] = ["no_primary_improvement"]
    return payload


def _disable_decision(
    payload: dict[str, Any],
    factor: str,
    *,
    primary_non_inferior: bool,
    guardrail_ok: bool,
) -> dict[str, Any]:
    if not primary_non_inferior:
        payload["status"] = "pass_enable"
        payload["recommendation"] = "enable"
        payload["reason_codes"] = ["graph_removal_degrades_quality"]
        return payload
    if not guardrail_ok:
        payload["status"] = "reject"
        payload["recommendation"] = "keep_disabled"
        payload["reason_codes"] = ["guardrail_regression"]
        return payload
    payload["status"] = "pass_disable"
    payload["recommendation"] = "degrade_to_document_only"
    payload["reason_codes"] = ["graph_removal_non_inferior"]
    payload["_recommended_config"] = dict(_FACTOR_CONFIG.get(factor, {}))
    return payload


def _paired_metric_values(
    baseline: Any, variant: Any
) -> dict[str, dict[str, list[float]]]:
    base_cases = {
        str(getattr(case, "case_id", "")): case
        for case in (getattr(baseline, "cases", ()) or ())
    }
    variant_cases = {
        str(getattr(case, "case_id", "")): case
        for case in (getattr(variant, "cases", ()) or ())
    }
    result: dict[str, dict[str, list[float]]] = {}
    for case_id in sorted(set(base_cases) & set(variant_cases)):
        base_case = base_cases[case_id]
        variant_case = variant_cases[case_id]
        for metric in (*PRIMARY_METRICS, *GUARDRAIL_METRICS):
            base_value = _case_metric(base_case, metric)
            variant_value = _case_metric(variant_case, metric)
            if base_value is None or variant_value is None:
                continue
            bucket = result.setdefault(
                metric,
                {"baseline": [], "variant": [], "diff": []},
            )
            bucket["baseline"].append(base_value)
            bucket["variant"].append(variant_value)
            bucket["diff"].append(variant_value - base_value)
    return result


def _case_metric(case: Any, name: str) -> float | None:
    injection = getattr(case, "injection", None)
    metadata = getattr(case, "metadata", None) or {}
    if name == "observed_latency_ms":
        return _finite(getattr(case, "observed_latency_ms", None))
    if name == "observed_provider_calls":
        advanced = getattr(case, "advanced_metrics", None) or {}
        return _finite(advanced.get("observed_provider_calls"))
    if name == "annotated_answer_correctness":
        return _finite(metadata.get("annotated_answer_correctness"))
    if not isinstance(injection, Mapping):
        return None
    if name == "final_injected_hit_rate":
        value = injection.get("final_injected_hit")
        return 1.0 if value is True else 0.0 if value is False else None
    if name == "injection_precision":
        return _finite(injection.get("injection_precision"))
    if name == "required_fact_coverage":
        return _finite(injection.get("required_fact_coverage"))
    if name == "negative_injection_rate":
        value = injection.get("negative_injection")
        return 1.0 if value is True else 0.0 if value is False else None
    return None


def _metric_summary(
    name: str,
    values: Mapping[str, Sequence[float]],
    policy: EffectGatePolicy,
    *,
    confidence: float,
    seed: int,
) -> dict[str, Any]:
    baseline = list(values.get("baseline", ()))
    variant = list(values.get("variant", ()))
    differences = list(values.get("diff", ()))
    if len(differences) < 2:
        return {
            "available": False,
            "pairs": len(differences),
            "margin": _margin(name, {"baseline": _mean_or_none(baseline)}, policy),
        }
    if name == "observed_latency_ms":
        interval = paired_p95_delta_bootstrap_ci(
            list(zip(baseline, variant, strict=True)),
            confidence=confidence,
            iterations=policy.bootstrap_iterations,
            seed=seed,
        )
        if interval is None:
            return {"available": False, "pairs": len(differences)}
        ci_low, ci_high = interval
        baseline_mean = _mean_or_none(baseline)
        delta = _mean_or_none(differences)
    else:
        interval = paired_bootstrap_mean_ci(
            differences,
            confidence=confidence,
            iterations=policy.bootstrap_iterations,
            seed=seed,
            rng="lcg",
        )
        if interval is None:
            return {"available": False, "pairs": len(differences)}
        ci_low, ci_high = interval
        baseline_mean = _mean_or_none(baseline)
        delta = _mean_or_none(differences)
    return {
        "available": True,
        "pairs": len(differences),
        "baseline": baseline_mean,
        "variant": _mean_or_none(variant),
        "delta": delta,
        "ci_low": round(ci_low, 6),
        "ci_high": round(ci_high, 6),
        "margin": _margin(name, {"baseline": baseline_mean}, policy),
    }


def _margin(
    name: str,
    metric: Mapping[str, Any],
    policy: EffectGatePolicy,
) -> float:
    if name == "observed_latency_ms":
        baseline = _finite(metric.get("baseline")) or 0.0
        return max(
            policy.latency_margin_ms,
            abs(baseline) * policy.latency_margin_ratio,
        )
    if name == "observed_provider_calls":
        return policy.provider_calls_margin
    if name == "negative_injection_rate":
        return policy.negative_injection_margin
    return policy.rate_margin


def _category_metrics(baseline: Any, variant: Any) -> dict[str, dict[str, Any]]:
    base_cases = {
        str(getattr(case, "case_id", "")): case
        for case in (getattr(baseline, "cases", ()) or ())
    }
    variant_cases = {
        str(getattr(case, "case_id", "")): case
        for case in (getattr(variant, "cases", ()) or ())
    }
    grouped: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
    for case_id in sorted(set(base_cases) & set(variant_cases)):
        base_case = base_cases[case_id]
        for category in _categories(getattr(base_case, "metadata", {}) or {}):
            grouped[category].append((base_case, variant_cases[case_id]))
    result: dict[str, dict[str, Any]] = {}
    for category, pairs in sorted(grouped.items()):
        metrics: dict[str, Any] = {"case_count": len(pairs)}
        for name in (*PRIMARY_METRICS, *GUARDRAIL_METRICS):
            base_values = [
                value
                for base_case, _ in pairs
                if (value := _case_metric(base_case, name)) is not None
            ]
            variant_values = [
                value
                for _, variant_case in pairs
                if (value := _case_metric(variant_case, name)) is not None
            ]
            metrics[name] = {
                "baseline": _mean_or_none(base_values),
                "variant": _mean_or_none(variant_values),
                "delta": _delta(base_values, variant_values),
            }
        result[category] = metrics
    return result


def _categories(metadata: Mapping[str, Any]) -> list[str]:
    raw = metadata.get("categories")
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = [str(item) for item in raw]
    else:
        values = [
            metadata.get("category"),
            metadata.get("evaluation_group"),
            metadata.get("scenario"),
        ]
    categories = sorted(
        {
            str(value).strip().casefold()
            for value in values
            if value is not None and str(value).strip()
        }
    )
    return categories or ["uncategorized"]


def _is_not_applicable(reason_code: str) -> bool:
    return reason_code in {
        "equivalent_to_baseline",
        "missing_graph_retriever",
        "missing_atom_retriever",
        "missing_reranker_dependency",
        "missing_dual_route",
    }


def _seed(factor: str, metric: str) -> int:
    digest = hashlib.sha256(f"{factor}:{metric}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _mean_or_none(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _delta(baseline: Sequence[float], variant: Sequence[float]) -> float | None:
    if not baseline or len(baseline) != len(variant):
        return None
    return round(
        sum(variant) / len(variant) - sum(baseline) / len(baseline),
        6,
    )


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
