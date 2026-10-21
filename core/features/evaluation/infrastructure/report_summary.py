"""评测报告的安全摘要与逐用例规范化。"""

from __future__ import annotations

from typing import Any

from ..application.retrieval_quality import EvaluationReport
from .report_payloads import normalize_json_value, safe_case_payload


def report_summary(report: EvaluationReport) -> dict[str, Any]:
    """从完整报告提取安全汇总指标。"""

    return {
        "total_cases": report.total_cases,
        "k": report.k,
        "recall_at_k": report.recall_at_k,
        "precision_at_k": report.precision_at_k,
        "mrr": report.mrr,
        "ndcg_at_k": report.ndcg_at_k,
        "multi_hop_recall": report.multi_hop_recall,
        "single_hop_recall": report.single_hop_recall,
        "noise_negative_false_hit": report.noise_negative_false_hit,
        "temporal_consistency": report.temporal_consistency,
        "conflict_accuracy": report.conflict_accuracy,
        "source_supported_projection_rate": report.source_supported_projection_rate,
        "observed_p50_latency_ms": report.observed_p50_latency_ms,
        "observed_p95_latency_ms": report.observed_p95_latency_ms,
        "annotated_p50_latency_ms": report.annotated_p50_latency_ms,
        "annotated_p95_latency_ms": report.annotated_p95_latency_ms,
        "reported_p50_latency_ms": report.reported_p50_latency_ms,
        "reported_p95_latency_ms": report.reported_p95_latency_ms,
        "annotated_answer_faithfulness": report.annotated_answer_faithfulness,
        "annotated_answer_relevancy": report.annotated_answer_relevancy,
        "judged_answer_faithfulness": report.judged_answer_faithfulness,
        "judged_answer_relevancy": report.judged_answer_relevancy,
        "reported_answer_faithfulness": report.reported_answer_faithfulness,
        "reported_answer_relevancy": report.reported_answer_relevancy,
        "observed_provider_calls": report.observed_provider_calls,
        "observed_token_cost": report.observed_token_cost,
        "annotated_provider_calls": report.annotated_provider_calls,
        "annotated_token_cost": report.annotated_token_cost,
        "reported_provider_calls": report.reported_provider_calls,
        "reported_token_cost": report.reported_token_cost,
        "reason_code_aggregates": dict(report.reason_code_aggregates),
        "injection_summary": normalize_json_value(report.injection_summary),
        "category_breakdown": normalize_json_value(report.category_breakdown),
        "dataset_breakdown": normalize_json_value(report.dataset_breakdown),
    }


def report_cases(report: EvaluationReport) -> list[dict[str, Any]]:
    """把逐用例 dataclass 规范化为可持久化 JSON 值。"""

    return [safe_case_payload(case) for case in report.cases]


__all__ = ["report_cases", "report_summary"]
