"""离线评测指标来源分类与聚合。"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    """检索器显式返回的文档和可验证运行时计数。"""

    documents: Sequence[Any]
    observed_provider_calls: float | None = None
    observed_token_cost: float | None = None


def split_retrieval_observation(
    value: Sequence[Any] | RetrievalObservation,
) -> tuple[list[Any], dict[str, float]]:
    """拆分检索文档与显式 instrumentation，非法计数按未观测处理。"""

    if not isinstance(value, RetrievalObservation):
        return list(value or []), {}

    metrics: dict[str, float] = {}
    for name in ("observed_provider_calls", "observed_token_cost"):
        number = optional_nonnegative_number(getattr(value, name))
        if number is not None:
            metrics[name] = number
    return list(value.documents or []), metrics


def annotated_latency(metadata: Mapping[str, Any]) -> float | None:
    """读取明确标为人工标注的延迟。"""

    return optional_nonnegative_number(metadata.get("annotated_latency_ms"))


def reported_latency(metadata: Mapping[str, Any]) -> float | None:
    """把旧版无来源延迟归类为不可独立验证的外部报告值。"""

    return optional_nonnegative_number(metadata.get("latency_ms"))


def build_case_metrics(
    *,
    metadata: Mapping[str, Any],
    ranked_doc_ids: Sequence[str],
    relevant_doc_ids: Iterable[Any],
    k: int,
    observed_metrics: Mapping[str, float],
) -> dict[str, float]:
    """计算单用例高级质量指标并保留每个可选数值的来源。"""

    top_k = {_normalize_doc_id(item) for item in ranked_doc_ids[:k]}
    relevant = _normalize_doc_id_set(relevant_doc_ids)
    hit = len(top_k & relevant) / len(relevant) if relevant else 0.0
    group = str(
        metadata.get("evaluation_group") or metadata.get("scenario") or ""
    ).lower()
    metrics: dict[str, float] = {}

    if group in {"multi_hop", "多跳"} or metadata.get("requires_relation") is True:
        metrics["multi_hop_recall"] = hit
    if group in {"single_hop", "direct", "single-hop", "单跳"}:
        metrics["single_hop_recall"] = hit
    if metadata.get("expected_no_hit") is True:
        metrics["noise_negative_false_hit"] = 1.0 if top_k else 0.0

    _append_temporal_metric(metrics, metadata, top_k)
    _append_conflict_metric(metrics, metadata, top_k)
    _append_projection_metric(metrics, metadata, top_k)
    _append_sourced_fixture_metrics(metrics, metadata)
    metrics.update(observed_metrics)
    return {key: round(value, 4) for key, value in metrics.items()}


def aggregate_case_metrics(
    values: Sequence[Mapping[str, float]],
) -> dict[str, float | None]:
    """聚合高级指标；未提供的来源保持 ``None``，成本计数按总量汇总。"""

    quality_names = (
        "multi_hop_recall",
        "single_hop_recall",
        "noise_negative_false_hit",
        "temporal_consistency",
        "conflict_accuracy",
        "source_supported_projection_rate",
    )
    optional_mean_names = (
        "annotated_answer_faithfulness",
        "annotated_answer_relevancy",
        "reported_answer_faithfulness",
        "reported_answer_relevancy",
    )
    total_names = (
        "observed_provider_calls",
        "observed_token_cost",
        "annotated_provider_calls",
        "annotated_token_cost",
        "reported_provider_calls",
        "reported_token_cost",
    )
    aggregated: dict[str, float | None] = {
        name: _mean(item[name] for item in values if name in item) or 0.0
        for name in quality_names
    }
    for name in optional_mean_names:
        aggregated[name] = _optional_mean(item[name] for item in values if name in item)
    for name in total_names:
        aggregated[name] = _optional_sum(item[name] for item in values if name in item)
    return aggregated


def reason_code_aggregates(
    metadata_items: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """按固定 reason code 汇总用例数量。"""

    counts: dict[str, int] = defaultdict(int)
    for metadata in metadata_items:
        reasons = metadata.get("reason_codes", [])
        if isinstance(reasons, str):
            reasons = [reasons]
        if not isinstance(reasons, Iterable):
            continue
        for reason in reasons:
            normalized = str(reason or "").strip()
            if normalized:
                counts[normalized] += 1
    return dict(sorted(counts.items()))


def optional_nonnegative_number(value: Any) -> float | None:
    """只接受非负有限数值，并保留真实零值。"""

    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def _append_temporal_metric(
    metrics: dict[str, float],
    metadata: Mapping[str, Any],
    ranked: set[str],
) -> None:
    """追加时态一致性指标。"""

    expected = _normalize_doc_id_set(metadata.get("temporal_expected_doc_ids", []))
    forbidden = _normalize_doc_id_set(metadata.get("temporal_forbidden_doc_ids", []))
    relevant = _normalize_doc_id_set(metadata.get("temporal_relevant_doc_ids", []))
    if expected or forbidden:
        metrics["temporal_consistency"] = (
            1.0 if expected <= ranked and not (forbidden & ranked) else 0.0
        )
    elif relevant:
        metrics["temporal_consistency"] = 1.0 if ranked & relevant else 0.0
    elif "temporal_consistency" in metadata:
        metrics["temporal_consistency"] = _bounded_number(
            metadata["temporal_consistency"]
        )


def _append_conflict_metric(
    metrics: dict[str, float],
    metadata: Mapping[str, Any],
    ranked: set[str],
) -> None:
    """追加冲突解析准确率指标。"""

    expected = _normalize_doc_id_set(metadata.get("conflict_expected_doc_ids", []))
    conflict_ids = _normalize_doc_id_set(metadata.get("conflict_doc_ids", []))
    if expected:
        metrics["conflict_accuracy"] = 1.0 if expected <= ranked else 0.0
    elif conflict_ids:
        metrics["conflict_accuracy"] = 1.0 if conflict_ids <= ranked else 0.0
    elif "conflict_accuracy" in metadata:
        metrics["conflict_accuracy"] = _bounded_number(metadata["conflict_accuracy"])


def _append_projection_metric(
    metrics: dict[str, float],
    metadata: Mapping[str, Any],
    ranked: set[str],
) -> None:
    """追加有 canonical 来源支持的 Projection 命中率。"""

    sources = _normalize_doc_id_set(metadata.get("projection_source_ids", []))
    if sources:
        metrics["source_supported_projection_rate"] = len(sources & ranked) / len(
            sources
        )
    elif "source_supported_projection_rate" in metadata:
        metrics["source_supported_projection_rate"] = _bounded_number(
            metadata["source_supported_projection_rate"]
        )


def _append_sourced_fixture_metrics(
    metrics: dict[str, float], metadata: Mapping[str, Any]
) -> None:
    """把明确标注值和旧外部值写入不同前缀。"""

    bounded_names = ("answer_faithfulness", "answer_relevancy")
    total_names = ("provider_calls", "token_cost")
    for name in bounded_names:
        annotated = optional_nonnegative_number(metadata.get(f"annotated_{name}"))
        reported = optional_nonnegative_number(metadata.get(name))
        if annotated is not None:
            metrics[f"annotated_{name}"] = min(1.0, annotated)
        if reported is not None:
            metrics[f"reported_{name}"] = min(1.0, reported)
    for name in total_names:
        annotated = optional_nonnegative_number(metadata.get(f"annotated_{name}"))
        reported = optional_nonnegative_number(metadata.get(name))
        if annotated is not None:
            metrics[f"annotated_{name}"] = annotated
        if reported is not None:
            metrics[f"reported_{name}"] = reported


def _normalize_doc_id_set(values: Iterable[Any]) -> set[str]:
    """把文档标识集合转换为稳定字符串集合。"""

    return {
        doc_id for doc_id in (_normalize_doc_id(value) for value in values) if doc_id
    }


def _normalize_doc_id(value: Any) -> str:
    """把单个文档标识转换为稳定字符串。"""

    return str(value or "").strip()


def _bounded_number(value: Any) -> float:
    """把可选质量标注限制到 0..1。"""

    number = optional_nonnegative_number(value)
    return min(1.0, number) if number is not None else 0.0


def _mean(values: Iterable[float]) -> float:
    """计算有限数值均值，空集合返回零。"""

    items = list(values)
    return round(sum(items) / len(items), 4) if items else 0.0


def _optional_mean(values: Iterable[float]) -> float | None:
    """计算可选数值均值，空集合保持不可用。"""

    items = list(values)
    return round(sum(items) / len(items), 4) if items else None


def _optional_sum(values: Iterable[float]) -> float | None:
    """计算可选运行计数总量，空集合保持不可用。"""

    items = list(values)
    return round(sum(items), 4) if items else None


__all__ = [
    "RetrievalObservation",
    "aggregate_case_metrics",
    "annotated_latency",
    "build_case_metrics",
    "optional_nonnegative_number",
    "reason_code_aggregates",
    "reported_latency",
    "split_retrieval_observation",
]
