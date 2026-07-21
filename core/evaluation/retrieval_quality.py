"""离线检索质量评测辅助函数。"""

from __future__ import annotations

import inspect
import json
import math
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvaluationCase:
    """一条检索评测查询及其相关文档标识。"""

    case_id: str
    query: str
    relevant_doc_ids: set[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievedDocument:
    """评测器使用的最小检索文档结构。"""

    doc_id: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationResult:
    """单个样本的检索质量结果。"""

    case_id: str
    query: str
    ranked_doc_ids: list[str]
    relevant_doc_ids: set[str]
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    latency_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)
    precision_at_k: float = 0.0
    advanced_metrics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationReport:
    """聚合后的检索质量报告。"""

    total_cases: int
    k: int
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    p95_latency_ms: float | None
    cases: list[EvaluationResult]
    dataset_breakdown: dict[str, dict[str, float | int]]
    precision_at_k: float = 0.0
    multi_hop_recall: float = 0.0
    single_hop_recall: float = 0.0
    noise_negative_false_hit: float = 0.0
    temporal_consistency: float = 0.0
    conflict_accuracy: float = 0.0
    source_supported_projection_rate: float = 0.0
    answer_faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    p50_latency_ms: float | None = None
    provider_calls: float = 0.0
    token_cost: float = 0.0
    reason_code_aggregates: dict[str, int] = field(default_factory=dict)

    @property
    def metrics(self) -> dict[str, Any]:
        """返回报告中的统一质量与成本指标。"""
        return {
            "recall_at_k": self.recall_at_k,
            "precision_at_k": self.precision_at_k,
            "mrr": self.mrr,
            "ndcg_at_k": self.ndcg_at_k,
            "multi_hop_recall": self.multi_hop_recall,
            "single_hop_recall": self.single_hop_recall,
            "noise_negative_false_hit": self.noise_negative_false_hit,
            "temporal_consistency": self.temporal_consistency,
            "conflict_accuracy": self.conflict_accuracy,
            "source_supported_projection_rate": self.source_supported_projection_rate,
            "answer_faithfulness": self.answer_faithfulness,
            "answer_relevancy": self.answer_relevancy,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "provider_calls": self.provider_calls,
            "token_cost": self.token_cost,
            "reason_code_aggregates": dict(self.reason_code_aggregates),
        }


@dataclass(slots=True)
class VariantComparison:
    """一次消融运行的评测报告及指标差异。"""

    baseline: "AblationReport"
    variants: dict[str, "AblationReport"]
    deltas: dict[str, dict[str, float | str | None]]
    reports: dict[str, EvaluationReport]


@dataclass(slots=True)
class AblationReport:
    """消融实验可比较的最小指标集合。"""

    name: str
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    p95_latency_ms: float | None

    @classmethod
    def from_metrics(
        cls,
        *,
        name: str,
        recall_at_k: float,
        mrr: float,
        ndcg_at_k: float,
        p95_latency_ms: float | None,
    ) -> "AblationReport":
        return cls(
            name=name,
            recall_at_k=round(float(recall_at_k), 4),
            mrr=round(float(mrr), 4),
            ndcg_at_k=round(float(ndcg_at_k), 4),
            p95_latency_ms=None if p95_latency_ms is None else round(float(p95_latency_ms), 4),
        )

    @classmethod
    def from_evaluation_report(
        cls,
        name: str,
        report: EvaluationReport,
    ) -> "AblationReport":
        return cls.from_metrics(
            name=name,
            recall_at_k=report.recall_at_k,
            mrr=report.mrr,
            ndcg_at_k=report.ndcg_at_k,
            p95_latency_ms=report.p95_latency_ms,
        )


RetrieverFn = Callable[[EvaluationCase, int], Sequence[Any] | Awaitable[Sequence[Any]]]


def make_memory_engine_retriever(engine: Any) -> RetrieverFn:
    """将 MemoryEngine 类对象适配为评测器检索协议。

    样本元数据会映射到 ``MemoryEngine.search_memories`` 的关键字参数，
    以便离线夹具覆盖私聊/群聊上下文、记忆类型过滤、用户个性化、情绪上下文
    和链路深度。
    """

    async def _retriever(case: EvaluationCase, k: int) -> Sequence[Any]:
        metadata = case.metadata or {}
        kwargs = dict(
            query=case.query,
            k=k,
            session_id=_optional_string(metadata.get("session_id")),
            persona_id=_optional_string(metadata.get("persona_id")),
            user_id=_optional_string(metadata.get("user_id")),
            chat_type=_optional_string(metadata.get("chat_type")) or "private",
            memory_types=_optional_string_list(metadata.get("memory_types")),
            emotion_context=_optional_string_list(metadata.get("emotion_context")),
            recall_type=_optional_string(metadata.get("recall_type")) or "passive",
            chain_depth=_optional_int(metadata.get("chain_depth"), default=0),
            query_intent=metadata.get("query_intent"),
            recall_strategy=metadata.get("recall_strategy"),
        )
        if "reference_time" in metadata:
            kwargs["reference_time"] = _parse_reference_time(
                metadata.get("reference_time")
            )
        return await engine.search_memories(**kwargs)

    return _retriever


async def evaluate_variants(
    cases: Sequence[EvaluationCase],
    variants: Mapping[str, RetrieverFn],
    *,
    k: int,
    baseline_name: str | None = None,
) -> VariantComparison:
    """评测多个检索变体并返回相对基线的差异。"""
    if not variants:
        raise ValueError("At least one retrieval variant is required")

    names = list(variants)
    baseline_key = baseline_name or names[0]
    if baseline_key not in variants:
        raise ValueError(f"Unknown baseline variant: {baseline_key}")

    reports: dict[str, EvaluationReport] = {}
    for name, retriever in variants.items():
        reports[name] = await evaluate_cases(cases, retriever, k=k)

    baseline = AblationReport.from_evaluation_report(
        baseline_key,
        reports[baseline_key],
    )
    variant_reports: dict[str, AblationReport] = {}
    deltas: dict[str, dict[str, float | str | None]] = {}
    for name, report in reports.items():
        if name == baseline_key:
            continue
        ablation = AblationReport.from_evaluation_report(name, report)
        variant_reports[name] = ablation
        deltas[name] = compare_reports(baseline, ablation)

    return VariantComparison(
        baseline=baseline,
        variants=variant_reports,
        deltas=deltas,
        reports=reports,
    )


def load_jsonl_cases(path: str | Path) -> list[EvaluationCase]:
    """从 JSONL 文件加载检索评测样本。"""
    file_path = Path(path)
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {file_path}:{line_number}: {exc}") from exc
        cases.append(_case_from_payload(payload, file_path=file_path, line_number=line_number))
    return cases


def load_fixture_dir(
    path: str | Path,
    *,
    include_experimental: bool = False,
) -> dict[str, list[EvaluationCase]]:
    """加载标准检索夹具；实验专用数据集需显式 opt-in。"""
    root = Path(path)
    datasets: dict[str, list[EvaluationCase]] = {}
    for file_path in sorted(root.glob("*.jsonl")):
        if not include_experimental and file_path.stem in {
            "session_first",
            "derived_metadata",
            "feedback_ranking",
        }:
            continue
        cases = load_jsonl_cases(file_path)
        dataset_name = file_path.stem
        if cases:
            dataset_name = str(cases[0].metadata.get("dataset") or dataset_name)
        datasets[dataset_name] = cases
    return datasets


def compare_reports(
    baseline: AblationReport,
    variant: AblationReport,
) -> dict[str, float | str | None]:
    """返回变体相对基线的指标差异。"""
    return {
        "baseline": baseline.name,
        "variant": variant.name,
        "recall_at_k_delta": round(variant.recall_at_k - baseline.recall_at_k, 4),
        "mrr_delta": round(variant.mrr - baseline.mrr, 4),
        "ndcg_at_k_delta": round(variant.ndcg_at_k - baseline.ndcg_at_k, 4),
        "p95_latency_ms_delta": _nullable_delta(
            baseline.p95_latency_ms,
            variant.p95_latency_ms,
        ),
    }


def recall_at_k(
    ranked_doc_ids: Sequence[Any],
    relevant_doc_ids: Iterable[Any],
    *,
    k: int,
) -> float:
    """返回前 K 个结果中是否出现任一相关文档。

    这是查询级 Recall@K，而不是对全部相关标签计算集合召回率；它回答的是：
    本次查询是否在前 K 个结果内找到了至少一条已知相关记忆？
    """
    relevant = _normalize_doc_id_set(relevant_doc_ids)
    if not relevant or k <= 0:
        return 0.0
    top_k = {_normalize_doc_id(doc_id) for doc_id in ranked_doc_ids[:k]}
    return 1.0 if top_k & relevant else 0.0


def reciprocal_rank(
    ranked_doc_ids: Sequence[Any],
    relevant_doc_ids: Iterable[Any],
) -> float:
    """返回第一个相关结果的倒数排名。"""
    relevant = _normalize_doc_id_set(relevant_doc_ids)
    if not relevant:
        return 0.0
    for index, doc_id in enumerate(ranked_doc_ids, start=1):
        if _normalize_doc_id(doc_id) in relevant:
            return round(1.0 / index, 4)
    return 0.0


def ndcg_at_k(
    ranked_doc_ids: Sequence[Any],
    relevant_doc_ids: Iterable[Any],
    *,
    k: int,
) -> float:
    """计算二值相关性的 nDCG@K。"""
    relevant = _normalize_doc_id_set(relevant_doc_ids)
    if not relevant or k <= 0:
        return 0.0

    dcg = 0.0
    for index, doc_id in enumerate(ranked_doc_ids[:k], start=1):
        if _normalize_doc_id(doc_id) in relevant:
            dcg += 1.0 / math.log2(index + 1)

    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    if idcg <= 0:
        return 0.0
    return round(dcg / idcg, 4)


async def evaluate_cases(
    cases: Sequence[EvaluationCase],
    retriever: RetrieverFn,
    *,
    k: int,
) -> EvaluationReport:
    """使用异步或同步检索器评测给定样本的检索质量。"""
    results: list[EvaluationResult] = []
    for case in cases:
        started_at = time.perf_counter()
        retrieved = retriever(case, k)
        if inspect.isawaitable(retrieved):
            retrieved = await retrieved
        measured_latency_ms = (time.perf_counter() - started_at) * 1000.0
        ranked_doc_ids = [_document_id(item) for item in list(retrieved or [])[:k]]
        latency_ms = _latency_from_case(case, measured_latency_ms)
        case_metrics = _score_case(case, ranked_doc_ids, k=k)
        advanced_metrics = _advanced_case_metrics(case, ranked_doc_ids, k=k)
        result_metadata = dict(case.metadata)
        result_metadata["observed_metrics"] = dict(advanced_metrics)
        results.append(
            EvaluationResult(
                case_id=case.case_id,
                query=case.query,
                ranked_doc_ids=ranked_doc_ids,
                relevant_doc_ids=set(case.relevant_doc_ids),
                recall_at_k=case_metrics["recall_at_k"],
                reciprocal_rank=case_metrics["reciprocal_rank"],
                ndcg_at_k=case_metrics["ndcg_at_k"],
                latency_ms=round(latency_ms, 4),
                metadata=result_metadata,
                precision_at_k=case_metrics["precision_at_k"],
                advanced_metrics=advanced_metrics,
            )
        )

    advanced = _aggregate_advanced_metrics(results)

    return EvaluationReport(
        total_cases=len(results),
        k=k,
        recall_at_k=_mean(item.recall_at_k for item in results),
        mrr=_mean(item.reciprocal_rank for item in results),
        ndcg_at_k=_mean(item.ndcg_at_k for item in results),
        p95_latency_ms=_percentile([item.latency_ms for item in results], 95),
        cases=results,
        dataset_breakdown=_dataset_breakdown(results),
        precision_at_k=_mean(item.precision_at_k for item in results),
        multi_hop_recall=advanced["multi_hop_recall"],
        single_hop_recall=advanced["single_hop_recall"],
        noise_negative_false_hit=advanced["noise_negative_false_hit"],
        temporal_consistency=advanced["temporal_consistency"],
        conflict_accuracy=advanced["conflict_accuracy"],
        source_supported_projection_rate=advanced["source_supported_projection_rate"],
        answer_faithfulness=advanced["answer_faithfulness"],
        answer_relevancy=advanced["answer_relevancy"],
        p50_latency_ms=_percentile([item.latency_ms for item in results], 50),
        provider_calls=advanced["provider_calls"],
        token_cost=advanced["token_cost"],
        reason_code_aggregates=_reason_code_aggregates(results),
    )


def _case_from_payload(
    payload: dict[str, Any],
    *,
    file_path: Path,
    line_number: int,
) -> EvaluationCase:
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object at {file_path}:{line_number}")
    case_id = str(payload.get("case_id") or "").strip()
    query = str(payload.get("query") or "").strip()
    relevant = payload.get("relevant_doc_ids")
    if not case_id:
        raise ValueError(f"Missing case_id at {file_path}:{line_number}")
    if not query:
        raise ValueError(f"Missing query at {file_path}:{line_number}")
    if not isinstance(relevant, list) or not relevant:
        raise ValueError(f"Missing relevant_doc_ids at {file_path}:{line_number}")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return EvaluationCase(
        case_id=case_id,
        query=query,
        relevant_doc_ids=_normalize_doc_id_set(relevant),
        metadata=dict(metadata),
    )


def _document_id(item: Any) -> str:
    if isinstance(item, RetrievedDocument):
        return _normalize_doc_id(item.doc_id)
    if isinstance(item, dict):
        return _normalize_doc_id(
            item.get("doc_id")
            or item.get("id")
            or item.get("memory_id")
            or item.get("document_id")
        )
    return _normalize_doc_id(getattr(item, "doc_id", item))


def _normalize_doc_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_doc_id_set(values: Iterable[Any]) -> set[str]:
    return {doc_id for doc_id in (_normalize_doc_id(value) for value in values) if doc_id}


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        return None
    normalized = [_optional_string(item) for item in items]
    result = [item for item in normalized if item]
    return result or None


def _optional_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_reference_time(value: Any) -> datetime | None:
    """在不导入 AstrBot/运行时模型的评测包中解析 UTC as-of 时间。"""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _latency_from_case(case: EvaluationCase, measured_latency_ms: float) -> float:
    raw = case.metadata.get("latency_ms")
    try:
        latency = float(raw)
    except (TypeError, ValueError):
        latency = measured_latency_ms
    return max(latency, 0.0)


def _score_case(case: EvaluationCase, ranked_doc_ids: Sequence[str], *, k: int) -> dict[str, float]:
    if case.metadata.get("expected_no_hit") is True:
        score = 1.0 if not ranked_doc_ids[:k] else 0.0
        return {
            "recall_at_k": score,
            "reciprocal_rank": score,
            "ndcg_at_k": score,
            "precision_at_k": score,
        }

    top_k = ranked_doc_ids[:k]
    relevant = _normalize_doc_id_set(case.relevant_doc_ids)
    precision = (
        len({_normalize_doc_id(doc_id) for doc_id in top_k} & relevant) / len(top_k)
        if top_k
        else 0.0
    )
    return {
        "recall_at_k": recall_at_k(ranked_doc_ids, case.relevant_doc_ids, k=k),
        "reciprocal_rank": reciprocal_rank(ranked_doc_ids, case.relevant_doc_ids),
        "ndcg_at_k": ndcg_at_k(ranked_doc_ids, case.relevant_doc_ids, k=k),
        "precision_at_k": round(precision, 4),
    }


def _advanced_case_metrics(
    case: EvaluationCase,
    ranked_doc_ids: Sequence[str],
    *,
    k: int,
) -> dict[str, float]:
    """根据匿名标注计算可选的演化质量指标。"""
    metadata = case.metadata or {}
    top_k = [_normalize_doc_id(item) for item in ranked_doc_ids[:k]]
    ranked = set(top_k)
    hit = recall_at_k(top_k, case.relevant_doc_ids, k=k)
    group = str(metadata.get("evaluation_group") or metadata.get("scenario") or "").lower()
    metrics: dict[str, float] = {}
    if group in {"multi_hop", "多跳"} or metadata.get("requires_relation") is True:
        metrics["multi_hop_recall"] = hit
    if group in {"single_hop", "direct", "single-hop", "单跳"}:
        metrics["single_hop_recall"] = hit

    if metadata.get("expected_no_hit") is True:
        metrics["noise_negative_false_hit"] = 1.0 if top_k else 0.0

    temporal_expected = _normalize_doc_id_set(
        metadata.get("temporal_expected_doc_ids", [])
    )
    temporal_forbidden = _normalize_doc_id_set(
        metadata.get("temporal_forbidden_doc_ids", [])
    )
    temporal_ids = _normalize_doc_id_set(metadata.get("temporal_relevant_doc_ids", []))
    if temporal_expected or temporal_forbidden:
        metrics["temporal_consistency"] = (
            1.0
            if temporal_expected <= ranked and not (temporal_forbidden & ranked)
            else 0.0
        )
    elif temporal_ids:
        metrics["temporal_consistency"] = 1.0 if ranked & temporal_ids else 0.0
    elif "temporal_consistency" in metadata:
        metrics["temporal_consistency"] = _bounded_metric(metadata["temporal_consistency"])

    conflict_expected = _normalize_doc_id_set(
        metadata.get("conflict_expected_doc_ids", [])
    )
    conflict_ids = _normalize_doc_id_set(metadata.get("conflict_doc_ids", []))
    if conflict_expected:
        metrics["conflict_accuracy"] = 1.0 if conflict_expected <= ranked else 0.0
    elif conflict_ids:
        metrics["conflict_accuracy"] = 1.0 if conflict_ids <= ranked else 0.0
    elif "conflict_accuracy" in metadata:
        metrics["conflict_accuracy"] = _bounded_metric(metadata["conflict_accuracy"])

    projection_sources = _normalize_doc_id_set(metadata.get("projection_source_ids", []))
    if projection_sources:
        metrics["source_supported_projection_rate"] = (
            len(projection_sources & ranked) / len(projection_sources)
        )
    elif "source_supported_projection_rate" in metadata:
        metrics["source_supported_projection_rate"] = _bounded_metric(
            metadata["source_supported_projection_rate"]
        )

    for name in ("answer_faithfulness", "answer_relevancy"):
        if name in metadata:
            metrics[name] = _bounded_metric(metadata[name])

    for name in ("provider_calls", "token_cost"):
        if name in metadata:
            metrics[name] = _nonnegative_metric(metadata[name])
    return {key: round(value, 4) for key, value in metrics.items()}


def _aggregate_advanced_metrics(results: Sequence[EvaluationResult]) -> dict[str, float]:
    names = (
        "multi_hop_recall",
        "single_hop_recall",
        "noise_negative_false_hit",
        "temporal_consistency",
        "conflict_accuracy",
        "source_supported_projection_rate",
        "answer_faithfulness",
        "answer_relevancy",
    )
    aggregated = {
        name: _mean(
            result.advanced_metrics[name]
            for result in results
            if name in result.advanced_metrics
        )
        for name in names
    }
    aggregated["provider_calls"] = _mean(
        result.advanced_metrics["provider_calls"]
        for result in results
        if "provider_calls" in result.advanced_metrics
    )
    aggregated["token_cost"] = _mean(
        result.advanced_metrics["token_cost"]
        for result in results
        if "token_cost" in result.advanced_metrics
    )
    return aggregated


def _reason_code_aggregates(results: Sequence[EvaluationResult]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for result in results:
        reasons = result.metadata.get("reason_codes", [])
        if isinstance(reasons, str):
            reasons = [reasons]
        if not isinstance(reasons, Iterable):
            continue
        for reason in reasons:
            normalized = str(reason or "").strip()
            if normalized:
                counts[normalized] += 1
    return dict(sorted(counts.items()))


def _bounded_metric(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _nonnegative_metric(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(items) / len(items), 4)


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return round(sorted_values[0], 4)
    if p <= 0:
        return round(sorted_values[0], 4)
    if p >= 100:
        return round(sorted_values[-1], 4)
    index = (p / 100.0) * (len(sorted_values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(sorted_values[int(index)], 4)
    return round(
        sorted_values[lower] * (upper - index)
        + sorted_values[upper] * (index - lower),
        4,
    )


def _nullable_delta(
    baseline: float | None,
    variant: float | None,
) -> float | None:
    if baseline is None or variant is None:
        return None
    return round(float(variant) - float(baseline), 4)


def _dataset_breakdown(results: Sequence[EvaluationResult]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[EvaluationResult]] = defaultdict(list)
    for result in results:
        dataset = str(result.metadata.get("dataset") or "default")
        grouped[dataset].append(result)

    breakdown: dict[str, dict[str, float | int]] = {}
    for dataset, items in grouped.items():
        breakdown[dataset] = {
            "case_count": len(items),
            "recall_at_k": _mean(item.recall_at_k for item in items),
            "mrr": _mean(item.reciprocal_rank for item in items),
            "ndcg_at_k": _mean(item.ndcg_at_k for item in items),
            "p95_latency_ms": _percentile([item.latency_ms for item in items], 95) or 0.0,
        }
    return breakdown
