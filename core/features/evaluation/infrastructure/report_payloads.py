"""评测报告写读共用的 JSON 规范化与 allowlist。"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from typing import Any

from ..domain.quality_loop_manifest import sanitize_quality_loop_manifest_summary
from ..domain.quality_loop_stages import QUALITY_LOOP_STAGE_REASONS

_SAFE_CASE_NUMERIC_FIELDS: tuple[str, ...] = (
    "recall_at_k",
    "precision_at_k",
    "reciprocal_rank",
    "ndcg_at_k",
    "observed_latency_ms",
    "annotated_latency_ms",
    "reported_latency_ms",
)

_SAFE_ADVANCED_METRICS: frozenset[str] = frozenset(
    {
        "multi_hop_recall",
        "single_hop_recall",
        "noise_negative_false_hit",
        "temporal_consistency",
        "conflict_accuracy",
        "source_supported_projection_rate",
        "annotated_answer_faithfulness",
        "annotated_answer_relevancy",
        "reported_answer_faithfulness",
        "reported_answer_relevancy",
        "observed_provider_calls",
        "observed_token_cost",
        "annotated_provider_calls",
        "annotated_token_cost",
        "reported_provider_calls",
        "reported_token_cost",
    }
)
_SAFE_INJECTION_NUMERIC_FIELDS: tuple[str, ...] = (
    "candidate_count",
    "selected_count",
    "rejected_count",
    "source_rejected_count",
    "selection_dropped_count",
    "dropped_relevant",
    "injection_precision",
    "required_fact_coverage",
    "budget_utilization",
    "injection_simulation_ms",
)
_SAFE_INJECTION_BOOLEAN_FIELDS: tuple[str, ...] = (
    "candidate_hit",
    "final_injected_hit",
    "negative_injection",
)
_SAFE_INJECTION_ROUTE_NUMERIC_FIELDS: tuple[str, ...] = (
    "document_route_weight",
    "graph_route_weight",
    "atom_route_weight",
    "document_route_score_max",
    "graph_route_score_max",
    "atom_route_score_max",
)
_SAFE_INJECTION_PRESETS: frozenset[str] = frozenset(
    {"tool_first", "low_cost", "balanced", "quality"}
)
_SAFE_INJECTION_INTENTS: frozenset[str] = frozenset(
    {
        "fixed",
        "default",
        "factual",
        "relationship",
        "temporal",
        "preference",
        "contextual",
        "strategy",
    }
)
_SAFE_INJECTION_REASON_CODES: frozenset[str] = frozenset(
    {
        "MANUAL_SELECTED",
        "PROVIDER_TOOL_UNAVAILABLE",
        "AUTO_HISTORY_INTENT",
        "AUTO_LOW_CONTEXT_HEADROOM",
        "AUTO_FALLBACK",
        "AUTO_MEMORY_UNCERTAIN",
        "HYBRID_CLAMPED_MIN",
        "HYBRID_CLAMPED_MAX",
        "INVALID_CONFIG_FALLBACK",
    }
)
_SAFE_PAIR_OUTCOME_KEYS: frozenset[str] = frozenset(
    {
        "context_key_hash",
        "slot_order",
        "should_use_case_hash",
        "should_silence_case_hash",
        "should_use_hit",
        "should_silence_correct",
    }
)
_QUALITY_LOOP_EXPECTATIONS = frozenset({"should_use", "should_silence"})


def json_sort_key(value: Any) -> str:
    """生成集合元素的稳定 JSON 排序键。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize_json_value(value: Any) -> Any:
    """递归把数据类、集合和路径转换为稳定 JSON 值。"""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, os.PathLike):
        return os.fspath(value)

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: normalize_json_value(getattr(value, field.name))
            for field in fields(value)
        }

    if isinstance(value, Mapping):
        return {str(key): normalize_json_value(item) for key, item in value.items()}

    if isinstance(value, (set, frozenset)):
        normalized_items = [normalize_json_value(item) for item in value]
        return sorted(normalized_items, key=json_sort_key)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize_json_value(item) for item in value]

    return str(value)


def finite_number(value: Any) -> int | float | None:
    """只接受有限数值，避免把自由文本或非标准 JSON 数写入报告。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def _quality_loop_hash(value: Any) -> bool:
    """校验质量闭环对外载荷中的 SHA-256 指纹。"""

    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(char in "0123456789abcdef" for char in value)
    )


def safe_injection_payload(value: Any) -> dict[str, Any] | None:
    """把逐用例注入观察收敛到数值/布尔/闭集枚举。"""

    if not isinstance(value, Mapping):
        return None
    safe: dict[str, Any] = {}
    for key in _SAFE_INJECTION_NUMERIC_FIELDS:
        number = finite_number(value.get(key))
        if number is not None:
            safe[key] = number
    for key in _SAFE_INJECTION_BOOLEAN_FIELDS:
        if isinstance(value.get(key), bool):
            safe[key] = value[key]
    preset = str(value.get("resolved_preset") or "").strip().casefold()
    if preset in _SAFE_INJECTION_PRESETS:
        safe["resolved_preset"] = preset
    reasons = value.get("decision_reason_codes")
    if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)):
        safe_reasons = sorted(
            {
                str(reason)
                for reason in reasons
                if str(reason) in _SAFE_INJECTION_REASON_CODES
            }
        )
        if safe_reasons:
            safe["decision_reason_codes"] = safe_reasons
    route = value.get("route_signals")
    if isinstance(route, Mapping):
        safe_route: dict[str, Any] = {}
        for key in _SAFE_INJECTION_ROUTE_NUMERIC_FIELDS:
            number = finite_number(route.get(key))
            if number is not None:
                safe_route[key] = number
        intent = str(route.get("query_intent") or "").strip().casefold()
        if intent in _SAFE_INJECTION_INTENTS:
            safe_route["query_intent"] = intent
        if safe_route:
            safe["route_signals"] = safe_route
    return safe or None


def safe_case_payload(case: Any) -> dict[str, Any]:
    """把样本结果收敛到无 query、业务 ID 和身份字段的数值 allowlist。"""

    normalized = normalize_json_value(case)
    if not isinstance(normalized, Mapping):
        return {}
    payload: dict[str, Any] = {}
    case_id = normalized.get("case_id")
    if case_id is not None:
        payload["case_id"] = str(case_id)[:128]
    for key in _SAFE_CASE_NUMERIC_FIELDS:
        value = finite_number(normalized.get(key))
        if value is not None:
            payload[key] = value

    legacy_latency = finite_number(normalized.get("latency_ms"))
    if "reported_latency_ms" not in payload and legacy_latency is not None:
        payload["reported_latency_ms"] = legacy_latency

    advanced = normalized.get("advanced_metrics")
    if isinstance(advanced, Mapping):
        safe_advanced: dict[str, int | float] = {}
        legacy_mapping = {
            "answer_faithfulness": "reported_answer_faithfulness",
            "answer_relevancy": "reported_answer_relevancy",
            "provider_calls": "reported_provider_calls",
            "token_cost": "reported_token_cost",
        }
        for key, value in advanced.items():
            source_key = str(key)
            key_text = legacy_mapping.get(source_key, source_key)
            number = finite_number(value)
            if source_key in legacy_mapping and key_text in safe_advanced:
                continue
            if key_text in _SAFE_ADVANCED_METRICS and number is not None:
                safe_advanced[key_text] = number
        if safe_advanced:
            payload["advanced_metrics"] = safe_advanced

    injection = safe_injection_payload(normalized.get("injection"))
    if injection is not None:
        payload["injection"] = injection
    return payload


def safe_quality_loop_payload(value: Any) -> dict[str, Any] | None:
    """把质量闭环载荷收敛到哈希/枚举/标量 allowlist（写读同口径）。"""

    normalized = normalize_json_value(value)
    if not isinstance(normalized, Mapping):
        return None
    sanitized: dict[str, Any] = {}

    manifest_value = normalized.get("manifest")
    if manifest_value is not None:
        manifest = sanitize_quality_loop_manifest_summary(manifest_value)
        if manifest is None:
            return None
        sanitized["manifest"] = manifest
    else:
        sanitized["manifest"] = None

    stages_raw = normalized.get("stages")
    if not isinstance(stages_raw, Sequence) or isinstance(stages_raw, (str, bytes)):
        return None
    from ..domain.quality_loop_stages import sanitize_stage_payload

    stages = [
        stage
        for stage in (sanitize_stage_payload(item) for item in stages_raw)
        if isinstance(stage, Mapping)
    ]
    if not stages:
        return None
    sanitized["stages"] = [dict(stage) for stage in stages]

    pairs = normalized.get("pairs")
    if not isinstance(pairs, Mapping):
        return None
    count = pairs.get("total_pairs")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    safe_pairs: dict[str, Any] = {"total_pairs": count}
    for key in ("should_use_hit_rate", "should_silence_correct_rate"):
        if key not in pairs:
            continue
        value = pairs[key]
        if value is None:
            safe_pairs[key] = None
        else:
            number = finite_number(value)
            if number is not None and 0.0 <= float(number) <= 1.0:
                safe_pairs[key] = number
    rejects = pairs.get("reject_reason_counts")
    if isinstance(rejects, Mapping):
        safe_rejects = {
            str(key): count
            for key, count in rejects.items()
            if str(key) in QUALITY_LOOP_STAGE_REASONS
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
        }
        safe_pairs["reject_reason_counts"] = dict(sorted(safe_rejects.items()))
    sanitized["pairs"] = safe_pairs

    outcomes_raw = normalized.get("pair_outcomes")
    if isinstance(outcomes_raw, Sequence) and not isinstance(
        outcomes_raw, (str, bytes)
    ):
        outcomes: list[dict[str, Any]] = []
        for item in outcomes_raw:
            if not isinstance(item, Mapping):
                continue
            safe_outcome: dict[str, Any] = {}
            for key in _SAFE_PAIR_OUTCOME_KEYS:
                entry = item.get(key)
                if key == "slot_order":
                    if isinstance(entry, Sequence) and not isinstance(
                        entry, (str, bytes)
                    ):
                        slots = [str(slot) for slot in entry]
                        if len(slots) == 2 and set(slots) == _QUALITY_LOOP_EXPECTATIONS:
                            safe_outcome[key] = slots
                    continue
                if key in {
                    "context_key_hash",
                    "should_use_case_hash",
                    "should_silence_case_hash",
                }:
                    if _quality_loop_hash(entry):
                        safe_outcome[key] = entry
                    continue
                if key in {"should_use_hit", "should_silence_correct"}:
                    if isinstance(entry, bool):
                        safe_outcome[key] = entry
            if safe_outcome:
                outcomes.append(safe_outcome)
        if outcomes:
            sanitized["pair_outcomes"] = outcomes

    return sanitized or None


__all__ = [
    "finite_number",
    "json_sort_key",
    "normalize_json_value",
    "safe_case_payload",
    "safe_injection_payload",
    "safe_quality_loop_payload",
]
