"""效果门报告的写读 allowlist 与紧凑摘要。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.effect_gate import REQUIRED_CATEGORIES
from .report_payloads import finite_number, normalize_json_value

EFFECT_GATE_SCHEMA = "effect-gate-v1"
EFFECT_GATE_BASELINE = "fixed_route_baseline"
EFFECT_GATE_FACTORS: tuple[str, ...] = (
    "atom",
    "graph_route",
    "dynamic_weights",
    "rerank",
)
EFFECT_GATE_VARIANTS: frozenset[str] = frozenset(
    {
        "atom_signal_on",
        "graph_route_off",
        "dynamic_route_weighting_on",
        "final_reranker_mmr",
    }
)
EFFECT_GATE_STATUSES: frozenset[str] = frozenset(
    {
        "pass_enable",
        "pass_disable",
        "reject",
        "insufficient_evidence",
        "not_applicable",
    }
)
EFFECT_GATE_RECOMMENDATIONS: frozenset[str] = frozenset(
    {
        "enable",
        "keep_disabled",
        "degrade_to_document_only",
        "collect_more_evidence",
    }
)
EFFECT_GATE_REASON_CODES: frozenset[str] = frozenset(
    {
        "available",
        "insufficient_categories",
        "missing_primary_metric",
        "primary_regression",
        "guardrail_regression",
        "pareto_improvement",
        "no_primary_improvement",
        "graph_removal_degrades_quality",
        "graph_removal_non_inferior",
        "execution_probe_missing",
        "variant_not_executed",
        "variant_not_exercised",
        "baseline_component_not_exercised",
        "equivalent_to_baseline",
        "missing_engine",
        "missing_engine_config",
        "missing_dual_route",
        "missing_derived_reader",
        "readonly_snapshot_cannot_activate_worker",
        "missing_document_vector_access",
        "missing_graph_retriever",
        "missing_atom_retriever",
        "missing_reranker_dependency",
        "variant_prepare_failed",
        "variant_execution_failed",
        "embedding_query_failed",
        "unknown_variant",
    }
)
EFFECT_GATE_METRICS: tuple[str, ...] = (
    "final_injected_hit_rate",
    "injection_precision",
    "required_fact_coverage",
    "negative_injection_rate",
    "observed_latency_ms",
    "observed_provider_calls",
)
EFFECT_GATE_CATEGORIES: frozenset[str] = frozenset(
    (*REQUIRED_CATEGORIES, "uncategorized")
)
_HASH_FIELDS: tuple[str, ...] = (
    "code_revision",
    "config_hash",
    "schema_hash",
    "fixture_hmac",
    "injection_config_hash",
)
_POLICY_NUMERIC_FIELDS: tuple[str, ...] = (
    "min_cases_per_category",
    "min_total_cases",
    "confidence",
    "bootstrap_iterations",
    "rate_margin",
    "latency_margin_ms",
    "latency_margin_ratio",
    "provider_calls_margin",
    "negative_injection_margin",
)


def _safe_count(value: Any) -> int | None:
    """只接受非负整数计数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_hash(value: Any) -> str | None:
    """只接受小写十六进制短哈希。"""

    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not 16 <= len(text) <= 128:
        return None
    if any(char not in "0123456789abcdef" for char in text):
        return None
    return text


def _safe_metric(value: Any) -> dict[str, Any] | None:
    """收敛单个指标的 available/pairs 与数值字段。"""

    if not isinstance(value, Mapping):
        return None
    available = value.get("available")
    pairs = _safe_count(value.get("pairs"))
    if not isinstance(available, bool) or pairs is None:
        return None
    safe: dict[str, Any] = {"available": available, "pairs": pairs}
    for key in ("baseline", "variant", "delta", "ci_low", "ci_high", "margin"):
        if key not in value:
            continue
        number = finite_number(value.get(key))
        safe[key] = number
    return safe


def _safe_metrics(value: Any) -> dict[str, Any]:
    """只保留六个已知指标。"""

    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for name in EFFECT_GATE_METRICS:
        metric = _safe_metric(value.get(name))
        if metric is not None:
            safe[name] = metric
    return safe


def _safe_category(value: Any) -> dict[str, Any] | None:
    """收敛单个类别的 case_count 与六个指标。"""

    if not isinstance(value, Mapping):
        return None
    case_count = _safe_count(value.get("case_count"))
    if case_count is None:
        return None
    safe: dict[str, Any] = {"case_count": case_count}
    for name in EFFECT_GATE_METRICS:
        metric = value.get(name)
        if not isinstance(metric, Mapping):
            continue
        safe_metric: dict[str, Any] = {}
        for key in ("baseline", "variant", "delta"):
            if key in metric:
                safe_metric[key] = finite_number(metric.get(key))
        if safe_metric:
            safe[name] = safe_metric
    return safe


def _safe_categories(value: Any) -> dict[str, Any]:
    """只保留七类必需类别与 uncategorized。"""

    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for category in EFFECT_GATE_CATEGORIES:
        item = _safe_category(value.get(category))
        if item is not None:
            safe[category] = item
    return safe


def _safe_reason_codes(value: Any) -> list[str]:
    """只保留闭集 reason code。"""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return sorted(
        {str(reason) for reason in value if str(reason) in EFFECT_GATE_REASON_CODES}
    )


def _safe_factor(value: Any) -> dict[str, Any] | None:
    """收敛单个因子的状态、建议、指标与类别。"""

    if not isinstance(value, Mapping):
        return None
    factor = str(value.get("factor") or "")
    variant = str(value.get("variant") or "")
    status = str(value.get("status") or "")
    recommendation = str(value.get("recommendation") or "")
    if factor not in EFFECT_GATE_FACTORS:
        return None
    if variant not in EFFECT_GATE_VARIANTS:
        return None
    if status not in EFFECT_GATE_STATUSES:
        return None
    if recommendation not in EFFECT_GATE_RECOMMENDATIONS:
        return None
    return {
        "factor": factor,
        "variant": variant,
        "status": status,
        "recommendation": recommendation,
        "reason_codes": _safe_reason_codes(value.get("reason_codes")),
        "metrics": _safe_metrics(value.get("metrics")),
        "categories": _safe_categories(value.get("categories")),
    }


def _safe_readiness(value: Any) -> dict[str, Any] | None:
    """收敛 readiness 的计数、缺失类别与可评估标记。"""

    if not isinstance(value, Mapping):
        return None
    required = value.get("required_categories")
    missing = value.get("missing")
    present = value.get("present_counts")
    total = _safe_count(value.get("total_cases"))
    can_evaluate = value.get("can_evaluate")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return None
    if not isinstance(missing, Sequence) or isinstance(missing, (str, bytes)):
        return None
    if not isinstance(present, Mapping) or total is None:
        return None
    if not isinstance(can_evaluate, bool):
        return None
    safe_present = {
        category: count
        for category in REQUIRED_CATEGORIES
        if (count := _safe_count(present.get(category))) is not None
    }
    return {
        "required_categories": [
            category for category in required if str(category) in REQUIRED_CATEGORIES
        ],
        "present_counts": safe_present,
        "total_cases": total,
        "missing": [
            category for category in missing if str(category) in REQUIRED_CATEGORIES
        ],
        "can_evaluate": can_evaluate,
    }


def _safe_policy(value: Any) -> dict[str, Any] | None:
    """收敛门禁策略到已知标量字段。"""

    if not isinstance(value, Mapping):
        return None
    safe: dict[str, Any] = {}
    version = value.get("policy_version")
    if isinstance(version, str) and 0 < len(version) <= 64:
        safe["policy_version"] = version
    required = value.get("required_categories")
    if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
        safe["required_categories"] = [
            category for category in required if str(category) in REQUIRED_CATEGORIES
        ]
    for key in _POLICY_NUMERIC_FIELDS:
        number = finite_number(value.get(key))
        if number is not None:
            safe[key] = number
    return safe or None


def _safe_manifest(value: Any) -> dict[str, Any] | None:
    """收敛 manifest 到哈希、计数与策略快照。"""

    if not isinstance(value, Mapping):
        return None
    safe: dict[str, Any] = {}
    if value.get("data_source") != "plugin_evaluation_datasets":
        return None
    safe["data_source"] = "plugin_evaluation_datasets"
    for key in _HASH_FIELDS:
        hashed = _safe_hash(value.get(key))
        if hashed is None:
            return None
        safe[key] = hashed
    db_hash = value.get("db_snapshot_hash")
    if db_hash is not None:
        db_hash = _safe_hash(db_hash)
        if db_hash is None:
            return None
    safe["db_snapshot_hash"] = db_hash
    for key in ("seed", "k", "case_count"):
        count = _safe_count(value.get(key))
        if count is None:
            return None
        safe[key] = count
    version = value.get("gate_policy_version")
    if not isinstance(version, str) or not 0 < len(version) <= 64:
        return None
    safe["gate_policy_version"] = version
    policy = _safe_policy(value.get("gate_policy"))
    if policy is None:
        return None
    safe["gate_policy"] = policy
    return safe


def _safe_canary(value: Any) -> dict[str, Any] | None:
    """只接受无命中的 privacy canary。"""

    if not isinstance(value, Mapping):
        return None
    checked = _safe_count(value.get("checked_values"))
    violations = value.get("violations")
    if checked is None or not isinstance(violations, Sequence):
        return None
    if isinstance(violations, (str, bytes)) or violations:
        return None
    return {"checked_values": checked, "violations": []}


def _safe_recommended_config(value: Any) -> dict[str, Any]:
    """只保留已知推荐配置键与固定值。"""

    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, Any] = {}
    atom_weight = value.get("atom_route_weight")
    if (
        isinstance(atom_weight, (int, float))
        and not isinstance(atom_weight, bool)
        and atom_weight == 0.25
    ):
        safe["atom_route_weight"] = 0.25
    if value.get("graph_retrieval_enabled") is False:
        safe["graph_retrieval_enabled"] = False
    if value.get("dynamic_route_weighting") is True:
        safe["dynamic_route_weighting"] = True
    if value.get("final_reranker") == "mmr":
        safe["final_reranker"] = "mmr"
    return safe


def safe_effect_gate_payload(value: Any) -> dict[str, Any] | None:
    """把效果门载荷收敛到闭集枚举、计数、数值与哈希（写读同口径）。"""

    normalized = normalize_json_value(value)
    if not isinstance(normalized, Mapping):
        return None
    if normalized.get("schema_version") != EFFECT_GATE_SCHEMA:
        return None
    if normalized.get("baseline") != EFFECT_GATE_BASELINE:
        return None

    readiness = _safe_readiness(normalized.get("readiness"))
    if readiness is None:
        return None
    factors_raw = normalized.get("factors")
    if not isinstance(factors_raw, Mapping):
        return None
    factors: dict[str, Any] = {}
    for factor in EFFECT_GATE_FACTORS:
        item = _safe_factor(factors_raw.get(factor))
        if item is not None:
            factors[factor] = item
    if set(factors) != set(EFFECT_GATE_FACTORS):
        return None

    policy = _safe_policy(normalized.get("policy"))
    manifest = _safe_manifest(normalized.get("manifest"))
    canary = _safe_canary(normalized.get("privacy_canary"))
    if policy is None or manifest is None or canary is None:
        return None
    return {
        "schema_version": EFFECT_GATE_SCHEMA,
        "baseline": EFFECT_GATE_BASELINE,
        "readiness": readiness,
        "factors": factors,
        "recommended_config": _safe_recommended_config(
            normalized.get("recommended_config")
        ),
        "policy": policy,
        "manifest": manifest,
        "privacy_canary": canary,
    }


def effect_gate_summary(value: Any) -> dict[str, Any] | None:
    """返回列表/对比使用的紧凑门禁摘要。"""

    safe = safe_effect_gate_payload(value)
    if safe is None:
        return None
    manifest = safe["manifest"]
    return {
        "schema_version": safe["schema_version"],
        "baseline": safe["baseline"],
        "readiness": safe["readiness"],
        "factors": {
            factor: {
                "status": item["status"],
                "recommendation": item["recommendation"],
                "reason_codes": item["reason_codes"],
            }
            for factor, item in safe["factors"].items()
        },
        "recommended_config": safe["recommended_config"],
        "manifest": {
            key: manifest.get(key)
            for key in (
                "code_revision",
                "config_hash",
                "schema_hash",
                "fixture_hmac",
                "db_snapshot_hash",
                "seed",
                "k",
                "gate_policy_version",
            )
        },
    }


__all__ = [
    "EFFECT_GATE_BASELINE",
    "EFFECT_GATE_CATEGORIES",
    "EFFECT_GATE_FACTORS",
    "EFFECT_GATE_METRICS",
    "EFFECT_GATE_REASON_CODES",
    "EFFECT_GATE_RECOMMENDATIONS",
    "EFFECT_GATE_SCHEMA",
    "EFFECT_GATE_STATUSES",
    "EFFECT_GATE_VARIANTS",
    "effect_gate_summary",
    "safe_effect_gate_payload",
]
