"""评测报告对比的指标提取与差值计算。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .effect_gate_payloads import effect_gate_summary

COMPARISON_METRICS: tuple[str, ...] = (
    "recall_at_k",
    "mrr",
    "ndcg_at_k",
    "observed_p95_latency_ms",
)
INJECTION_METRICS: tuple[str, ...] = (
    "final_injected_hit_rate",
    "injection_precision",
    "required_fact_coverage",
    "negative_injection_rate",
)


async def compare_evaluation_reports(
    store: Any,
    report_id_a: str,
    report_id_b: str,
) -> dict[str, Any] | None:
    """返回报告 A 到报告 B 的指标差异。"""

    if store is None:
        return None
    report_a = await store.get_report(report_id_a)
    report_b = await store.get_report(report_id_b)
    if report_a is None or report_b is None:
        return None

    summary_a = report_a.get("summary", {})
    summary_b = report_b.get("summary", {})
    return {
        "report_id_a": report_id_a,
        "report_id_b": report_id_b,
        "deltas": {
            metric: metric_delta(
                metric_value(summary_a, metric),
                metric_value(summary_b, metric),
            )
            for metric in COMPARISON_METRICS
        },
        "injection_deltas": {
            metric: metric_delta(
                injection_value(summary_a, metric),
                injection_value(summary_b, metric),
            )
            for metric in INJECTION_METRICS
        },
        "cost_deltas": {
            "observed_provider_calls": metric_delta(
                metric_value(summary_a, "observed_provider_calls"),
                metric_value(summary_b, "observed_provider_calls"),
            )
        },
        "effect_gate": compare_effect_gate(report_a, report_b),
        "reports": {
            "a": report_compare_summary(report_a),
            "b": report_compare_summary(report_b),
        },
    }


def metric_value(
    report_or_summary: Mapping[str, Any],
    metric: str,
) -> float | None:
    """将可选指标安全转换为浮点数。"""

    value = report_or_summary.get(metric)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def injection_value(
    summary: Mapping[str, Any],
    metric: str,
) -> float | None:
    """从嵌套 injection_summary 提取可选指标。"""

    injection = summary.get("injection_summary")
    if not isinstance(injection, Mapping):
        return None
    return metric_value(injection, metric)


def metric_delta(a: float | None, b: float | None) -> float | None:
    """返回 B-A 差值；任一值缺失时保持未知。"""

    if a is None or b is None:
        return None
    return round(b - a, 4)


def compare_effect_gate(
    report_a: Mapping[str, Any],
    report_b: Mapping[str, Any],
) -> dict[str, Any] | None:
    """比较两份报告的门禁状态；任一侧缺失时保持未知。"""

    gate_a = effect_gate_summary(report_a.get("effect_gate"))
    gate_b = effect_gate_summary(report_b.get("effect_gate"))
    if gate_a is None or gate_b is None:
        return None
    factors_a = gate_a["factors"]
    factors_b = gate_b["factors"]
    changed = sorted(
        factor
        for factor in set(factors_a) | set(factors_b)
        if factors_a.get(factor, {}).get("status")
        != factors_b.get(factor, {}).get("status")
        or factors_a.get(factor, {}).get("recommendation")
        != factors_b.get(factor, {}).get("recommendation")
    )
    return {
        "a": gate_a,
        "b": gate_b,
        "changed_factors": changed,
    }


def report_compare_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """提取报告对比端点需要的最小摘要。"""

    summary = dict(report.get("summary", {}) or {})
    gate_summary = effect_gate_summary(report.get("effect_gate"))
    if gate_summary is None:
        gate_summary = summary.get("effect_gate_summary")
    if gate_summary is not None:
        summary["effect_gate_summary"] = gate_summary
    return {
        "report_id": report.get("report_id"),
        "created_at": report.get("created_at"),
        "baseline": report.get("baseline"),
        "summary": summary,
    }


__all__ = [
    "COMPARISON_METRICS",
    "INJECTION_METRICS",
    "compare_effect_gate",
    "compare_evaluation_reports",
    "injection_value",
    "metric_delta",
    "metric_value",
    "report_compare_summary",
]
