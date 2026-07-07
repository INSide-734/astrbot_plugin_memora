"""Offline retrieval quality evaluation helpers."""

from __future__ import annotations

import inspect
import json
import math
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvaluationCase:
    """One retrieval evaluation query and its relevant document IDs."""

    case_id: str
    query: str
    relevant_doc_ids: set[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RetrievedDocument:
    """Minimal retrieved document shape used by the evaluator."""

    doc_id: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationResult:
    """Per-case retrieval quality result."""

    case_id: str
    query: str
    ranked_doc_ids: list[str]
    relevant_doc_ids: set[str]
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    latency_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationReport:
    """Aggregate retrieval quality report."""

    total_cases: int
    k: int
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    p95_latency_ms: float | None
    cases: list[EvaluationResult]
    dataset_breakdown: dict[str, dict[str, float | int]]


@dataclass(slots=True)
class VariantComparison:
    """Evaluation reports and deltas for one ablation run."""

    baseline: "AblationReport"
    variants: dict[str, "AblationReport"]
    deltas: dict[str, dict[str, float | str | None]]
    reports: dict[str, EvaluationReport]


@dataclass(slots=True)
class AblationReport:
    """Minimal comparable metric set for ablation studies."""

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
    """Adapt a MemoryEngine-like object to the evaluation retriever protocol.

    Case metadata is mapped to ``MemoryEngine.search_memories`` keyword
    arguments so offline fixtures can exercise private/group context, memory
    type filters, user personalization, emotion context, and chain depth.
    """

    async def _retriever(case: EvaluationCase, k: int) -> Sequence[Any]:
        metadata = case.metadata or {}
        return await engine.search_memories(
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

    return _retriever


async def evaluate_variants(
    cases: Sequence[EvaluationCase],
    variants: Mapping[str, RetrieverFn],
    *,
    k: int,
    baseline_name: str | None = None,
) -> VariantComparison:
    """Evaluate several retrieval variants and return baseline deltas."""
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
    """Load retrieval evaluation cases from a JSONL file."""
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


def load_fixture_dir(path: str | Path) -> dict[str, list[EvaluationCase]]:
    """Load all retrieval JSONL fixtures in a directory grouped by dataset name."""
    root = Path(path)
    datasets: dict[str, list[EvaluationCase]] = {}
    for file_path in sorted(root.glob("*.jsonl")):
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
    """Return metric deltas from baseline to variant."""
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
    """Return whether any relevant doc appears in the top-k results.

    This is query-level Recall@K for retrieval evaluation, not set recall over
    all relevant labels. It answers: did this query retrieve at least one known
    relevant memory within K?
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
    """Return reciprocal rank of the first relevant result."""
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
    """Compute binary-relevance nDCG@K."""
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
    """Evaluate retrieval quality for *cases* using an async or sync retriever."""
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
                metadata=dict(case.metadata),
            )
        )

    return EvaluationReport(
        total_cases=len(results),
        k=k,
        recall_at_k=_mean(item.recall_at_k for item in results),
        mrr=_mean(item.reciprocal_rank for item in results),
        ndcg_at_k=_mean(item.ndcg_at_k for item in results),
        p95_latency_ms=_percentile([item.latency_ms for item in results], 95),
        cases=results,
        dataset_breakdown=_dataset_breakdown(results),
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
        }

    return {
        "recall_at_k": recall_at_k(ranked_doc_ids, case.relevant_doc_ids, k=k),
        "reciprocal_rank": reciprocal_rank(ranked_doc_ids, case.relevant_doc_ids),
        "ndcg_at_k": ndcg_at_k(ranked_doc_ids, case.relevant_doc_ids, k=k),
    }


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
