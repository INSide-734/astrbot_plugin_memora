"""有限派生元数据的 process-local 索引与离线双变体评测。"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ....models.derived_metadata import (
    DerivedMetadataAnnotation,
    DerivedMetadataProposal,
    DerivedMetadataValidationResult,
    validate_derived_metadata_proposal,
)
from .retrieval_quality import (
    EvaluationCase,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

DERIVED_INDEX_REASON_CODES = frozenset(
    {
        "annotation_accepted",
        "annotation_schema_rejected",
        "annotation_budget_rejected",
        "annotation_prompt_like_rejected",
        "source_not_found",
        "source_revision_mismatch",
        "source_visibility_mismatch",
        "variant_not_exercised",
        "variant_execution_failed",
    }
)
_TERM_RE = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class DerivedMetadataMatch:
    """索引内部的 canonical 候选信号，不进入安全报告。"""

    memory_id: int
    signal: float
    field_hits: int


@dataclass(frozen=True, slots=True)
class DerivedMetadataIndexSummary:
    """索引生命周期内可安全汇总的计数和预算统计。"""

    accepted_count: int
    rejected_count: int
    stale_count: int
    matched_source_count: int
    successful_match_count: int
    reason_code: str
    max_budget_utilization: float


class RunLocalDerivedMetadataIndex:
    """绑定单次评测生命周期的 source-backed 倒排索引。"""

    def __init__(
        self, source_loader: Callable[[int], Mapping[str, Any] | None]
    ) -> None:
        """保存只读 source loader，不持有数据库连接或生产 Store。"""

        self._source_loader = source_loader
        self._annotations: dict[tuple[int, str], DerivedMetadataAnnotation] = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._stale_count = 0
        self._matched_source_count = 0
        self._successful_match_count = 0
        self._failure_reason: str | None = None
        self._max_budget_utilization = 0.0

    @property
    def reason_code(self) -> str:
        """返回跨 proposal 和查询执行聚合的稳定状态。"""

        return self._failure_reason or (
            "available" if self._successful_match_count else "variant_not_exercised"
        )

    def add_proposal(
        self,
        proposal: DerivedMetadataProposal | Mapping[str, Any],
    ) -> DerivedMetadataValidationResult:
        """验证并幂等加入一条 annotation；拒绝值不会进入索引。"""

        result = validate_derived_metadata_proposal(proposal)
        if not result.accepted or result.annotation is None:
            self._rejected_count += 1
            return result
        annotation = result.annotation
        key = (annotation.source.memory_id, annotation.source.revision_token)
        self._annotations[key] = annotation
        self._accepted_count = len(self._annotations)
        self._max_budget_utilization = max(
            self._max_budget_utilization,
            _budget_utilization(result),
        )
        return result

    def rebuild(
        self,
        proposals: Sequence[DerivedMetadataProposal | Mapping[str, Any]],
    ) -> list[DerivedMetadataValidationResult]:
        """清空运行内状态后按固定顺序重建相同规范化 annotation 集合。"""

        self.clear()
        return [self.add_proposal(proposal) for proposal in proposals]

    def clear(self) -> None:
        """释放本次评测的所有派生索引和计数。"""

        self._annotations.clear()
        self._accepted_count = 0
        self._rejected_count = 0
        self._stale_count = 0
        self._matched_source_count = 0
        self._successful_match_count = 0
        self._failure_reason = None
        self._max_budget_utilization = 0.0

    def match(
        self,
        query: str,
        context: Mapping[str, Any],
    ) -> list[DerivedMetadataMatch]:
        """对 query 做 exact token/phrase 匹配并重新校验 source 可见性。"""

        if not isinstance(query, str) or not query.strip():
            return []
        query_normalized = _normalize_query(query)
        query_terms = set(_TERM_RE.findall(query_normalized))
        matches: dict[int, DerivedMetadataMatch] = {}
        for annotation in self._annotations.values():
            try:
                source = self._source_loader(annotation.source.memory_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._record_failure("variant_execution_failed")
                continue
            visible, reason_code = _source_visible(annotation, source, context)
            if not visible:
                self._stale_count += 1
                self._record_failure(reason_code)
                continue
            field_hits = _field_hits(annotation, query_normalized, query_terms)
            if field_hits <= 0:
                continue
            signal = min(0.2, 0.05 * field_hits)
            previous = matches.get(annotation.source.memory_id)
            if previous is None or signal > previous.signal:
                matches[annotation.source.memory_id] = DerivedMetadataMatch(
                    annotation.source.memory_id,
                    signal,
                    field_hits,
                )
        ordered = sorted(
            matches.values(), key=lambda item: (-item.signal, item.memory_id)
        )
        self._matched_source_count += len(ordered)
        if ordered:
            self._successful_match_count += 1
        return ordered

    def summary(self) -> DerivedMetadataIndexSummary:
        """返回不含 query、source ID、revision 或 annotation 内容的安全摘要。"""

        return DerivedMetadataIndexSummary(
            accepted_count=self._accepted_count,
            rejected_count=self._rejected_count,
            stale_count=self._stale_count,
            matched_source_count=self._matched_source_count,
            successful_match_count=self._successful_match_count,
            reason_code=self.reason_code,
            max_budget_utilization=round(self._max_budget_utilization, 4),
        )

    def _record_failure(self, reason_code: str) -> None:
        """保留首个执行失败，避免后续成功覆盖 stale 证据。"""

        if reason_code in {
            "variant_execution_failed",
            "source_not_found",
            "source_revision_mismatch",
            "source_visibility_mismatch",
        }:
            self._failure_reason = self._failure_reason or reason_code


@dataclass(frozen=True, slots=True)
class DerivedMetadataBranchMetrics:
    """派生元数据变体的质量、延迟和成本聚合。"""

    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    observed_p50_latency_ms: float | None
    observed_p95_latency_ms: float | None
    annotated_p50_latency_ms: float | None
    annotated_p95_latency_ms: float | None
    observed_provider_calls: float | None = None
    observed_token_cost: float | None = None


@dataclass(frozen=True, slots=True)
class DerivedMetadataReport:
    """有限元数据离线双变体的安全报告。"""

    status: str
    reason_code: str
    total_cases: int
    k: int
    baseline: DerivedMetadataBranchMetrics
    bounded_variant: DerivedMetadataBranchMetrics
    accepted_count: int
    rejected_count: int
    stale_count: int
    matched_source_count: int
    macro_precision: float
    max_budget_utilization: float
    reason_code_aggregates: dict[str, int] = field(default_factory=dict)
    metadata_dependent_recall_delta: float = 0.0


async def run_derived_metadata_ablation(
    cases: Sequence[EvaluationCase],
    baseline_retriever: Callable[
        [EvaluationCase, int], Sequence[Any] | Awaitable[Sequence[Any]]
    ],
    proposals: Sequence[DerivedMetadataProposal | Mapping[str, Any]],
    source_loader: Callable[[int], Mapping[str, Any] | None],
    *,
    k: int,
    expected_annotation_keys: set[tuple[int, str]] | None = None,
) -> DerivedMetadataReport:
    """运行 baseline 与有限元数据变体，并在普通索引失败时回退 baseline。"""

    safe_k = max(1, min(int(k), 20))
    index = RunLocalDerivedMetadataIndex(source_loader)
    validation_results = index.rebuild(proposals)
    accepted_keys = {
        (result.annotation.source.memory_id, result.annotation.source.revision_token)
        for result in validation_results
        if result.accepted and result.annotation is not None
    }
    expected_keys = expected_annotation_keys or accepted_keys
    true_positive = len(accepted_keys & expected_keys)
    macro_precision = (
        round(true_positive / len(accepted_keys), 4) if accepted_keys else 0.0
    )
    baseline_rows: list[_BranchRow] = []
    variant_rows: list[_BranchRow] = []
    reason_counts: Counter[str] = Counter()
    dependent_baseline: list[float] = []
    dependent_variant: list[float] = []

    for case in cases:
        started = time.perf_counter()
        try:
            raw = baseline_retriever(case, safe_k)
            raw = await raw if inspect.isawaitable(raw) else raw
        except asyncio.CancelledError:
            raise
        baseline_latencies = _latencies(case, started, "baseline")
        baseline_candidates = _normalize_candidates(raw)
        baseline_rows.append(
            _BranchRow(case, baseline_candidates, *baseline_latencies, "baseline")
        )
        try:
            matches = index.match(case.query, case.metadata)
            variant_candidates = _augment_candidates(
                baseline_candidates,
                matches,
                source_loader,
                safe_k,
            )
            reason_counts[index.reason_code] += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            index._record_failure("variant_execution_failed")
            reason_counts["variant_execution_failed"] += 1
            variant_candidates = list(baseline_candidates)
        variant_latencies = _latencies(case, started, "variant")
        variant_rows.append(
            _BranchRow(case, variant_candidates, *variant_latencies, "variant")
        )
        if case.metadata.get("metadata_dependent") is True:
            dependent_baseline.append(_case_recall(case, baseline_candidates, safe_k))
            dependent_variant.append(_case_recall(case, variant_candidates, safe_k))

    baseline_metrics = _branch_metrics(baseline_rows, safe_k)
    variant_metrics = _branch_metrics(variant_rows, safe_k)
    summary = index.summary()
    status = (
        "skipped"
        if summary.reason_code in {"variant_execution_failed", "variant_not_exercised"}
        else "completed"
    )
    return DerivedMetadataReport(
        status=status,
        reason_code=summary.reason_code,
        total_cases=len(cases),
        k=safe_k,
        baseline=baseline_metrics,
        bounded_variant=variant_metrics,
        accepted_count=summary.accepted_count,
        rejected_count=summary.rejected_count,
        stale_count=summary.stale_count,
        matched_source_count=summary.matched_source_count,
        macro_precision=macro_precision,
        max_budget_utilization=summary.max_budget_utilization,
        reason_code_aggregates=dict(sorted(reason_counts.items())),
        metadata_dependent_recall_delta=round(
            _mean(dependent_variant) - _mean(dependent_baseline),
            4,
        ),
    )


@dataclass(frozen=True, slots=True)
class _Candidate:
    """索引编排使用的内部 canonical 候选。"""

    doc_id: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _BranchRow:
    """单分支评测的内部行。"""

    case: EvaluationCase
    candidates: list[_Candidate]
    observed_latency_ms: float
    annotated_latency_ms: float | None
    branch: str


def _normalize_candidates(items: Sequence[Any] | None) -> list[_Candidate]:
    """规范化 baseline 结果并丢弃无效分数。"""

    result: list[_Candidate] = []
    for item in list(items or []):
        if isinstance(item, Mapping):
            doc_id = item.get("doc_id") or item.get("id") or item.get("memory_id")
            score = item.get("final_score", item.get("score", 0.0))
            metadata = item.get("metadata")
        else:
            doc_id = getattr(item, "doc_id", item)
            score = getattr(item, "final_score", getattr(item, "score", 0.0))
            metadata = getattr(item, "metadata", None)
        try:
            number = float(score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number) or not str(doc_id or "").strip():
            continue
        result.append(_Candidate(str(doc_id).strip(), number, dict(metadata or {})))
    return result


def _augment_candidates(
    baseline: Sequence[_Candidate],
    matches: Sequence[DerivedMetadataMatch],
    source_loader: Callable[[int], Mapping[str, Any] | None],
    k: int,
) -> list[_Candidate]:
    """把有限信号加入 canonical 候选，保持分数上限和既有候选。"""

    candidates = {item.doc_id: item for item in baseline}
    for match in matches:
        source = source_loader(match.memory_id)
        if not isinstance(source, Mapping) or source.get("deleted") is True:
            continue
        doc_id = str(source.get("doc_id") or match.memory_id)
        base = candidates.get(doc_id)
        if base is None:
            base = _Candidate(
                doc_id,
                _finite_score(source.get("score", 0.0)),
                dict(source.get("metadata") or {}),
            )
        candidates[doc_id] = _Candidate(
            doc_id,
            min(1.0, max(0.0, base.score + match.signal)),
            dict(base.metadata),
        )
    return sorted(candidates.values(), key=lambda item: (-item.score, item.doc_id))[:k]


def _source_visible(
    annotation: DerivedMetadataAnnotation,
    source: Mapping[str, Any] | None,
    context: Mapping[str, Any],
) -> tuple[bool, str]:
    """重新核对 source revision、作用域、privacy、role 和有效期。"""

    if not isinstance(source, Mapping) or source.get("deleted") is True:
        return False, "source_not_found"
    if source.get("revision_token") != annotation.source.revision_token:
        return False, "source_revision_mismatch"
    for source_key, ref_value, context_key in (
        ("trusted_scope", annotation.source.trusted_scope, "scope"),
        ("privacy_level", annotation.source.privacy_level, "privacy_level"),
        ("source_role", annotation.source.source_role, "role"),
    ):
        if source.get(source_key) != ref_value or (
            context.get(context_key) is not None
            and context.get(context_key) != ref_value
        ):
            return False, "source_visibility_mismatch"
    if source.get("valid") is False:
        return False, "source_visibility_mismatch"
    reference_time = _parse_time(context.get("reference_time"))
    if not _valid_window(source, reference_time):
        return False, "source_visibility_mismatch"
    return True, "annotation_accepted"


def _field_hits(
    annotation: DerivedMetadataAnnotation,
    query: str,
    query_terms: set[str],
) -> int:
    """计算跨字段去重后的 exact token/phrase 命中数。"""

    hits = 0
    for value in (
        *annotation.keywords,
        *annotation.topic_tags,
        *annotation.context_labels,
    ):
        normalized = value.casefold()
        if normalized in query or normalized in query_terms:
            hits += 1
    return hits


def _normalize_query(query: str) -> str:
    """规范化当前调用内的 query，不写入 index 或报告。"""

    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


def _valid_window(source: Mapping[str, Any], reference_time: datetime | None) -> bool:
    """判断 source 是否在 reference time 下有效。"""

    if reference_time is None:
        return True
    valid_from = _parse_time(source.get("valid_from"))
    valid_to = _parse_time(source.get("valid_to"))
    return not (valid_from and reference_time < valid_from) and not (
        valid_to and reference_time >= valid_to
    )


def _parse_time(value: Any) -> datetime | None:
    """解析评测所需的 UTC ISO 时间，非法值按未知处理。"""

    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo
            else value.replace(tzinfo=timezone.utc)
        )
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.astimezone(timezone.utc)
        if parsed.tzinfo
        else parsed.replace(tzinfo=timezone.utc)
    )


def _budget_utilization(result: DerivedMetadataValidationResult) -> float:
    """把验证结果转换为不含原文的预算利用率。"""

    return max(
        result.total_items / 16,
        result.total_chars / 256,
        result.json_bytes / 1024,
    )


def _branch_metrics(rows: Sequence[_BranchRow], k: int) -> DerivedMetadataBranchMetrics:
    """聚合现有 Recall@K、MRR、nDCG 和匿名延迟成本。"""

    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    observed_latencies: list[float] = []
    annotated_latencies: list[float] = []
    for row in rows:
        ranked = [item.doc_id for item in row.candidates]
        if row.case.metadata.get("expected_no_hit") is True:
            score = 1.0 if not ranked[:k] else 0.0
            recalls.append(score)
            mrrs.append(score)
            ndcgs.append(score)
        else:
            recalls.append(recall_at_k(ranked, row.case.relevant_doc_ids, k=k))
            mrrs.append(reciprocal_rank(ranked, row.case.relevant_doc_ids))
            ndcgs.append(ndcg_at_k(ranked, row.case.relevant_doc_ids, k=k))
        observed_latencies.append(row.observed_latency_ms)
        if row.annotated_latency_ms is not None:
            annotated_latencies.append(row.annotated_latency_ms)
    return DerivedMetadataBranchMetrics(
        recall_at_k=_mean(recalls),
        mrr=_mean(mrrs),
        ndcg_at_k=_mean(ndcgs),
        observed_p50_latency_ms=_percentile(observed_latencies, 50),
        observed_p95_latency_ms=_percentile(observed_latencies, 95),
        annotated_p50_latency_ms=_percentile(annotated_latencies, 50),
        annotated_p95_latency_ms=_percentile(annotated_latencies, 95),
    )


def _case_recall(
    case: EvaluationCase, candidates: Sequence[_Candidate], k: int
) -> float:
    """计算单用例 Recall@K。"""

    return recall_at_k([item.doc_id for item in candidates], case.relevant_doc_ids, k=k)


def _latencies(
    case: EvaluationCase, started: float, branch: str
) -> tuple[float, float | None]:
    """返回墙钟实测延迟和可选的分支人工标注延迟。"""

    observed = max(0.0, (time.perf_counter() - started) * 1000)
    raw = case.metadata.get(f"annotated_{branch}_latency_ms")
    try:
        annotated = (
            float(raw) if raw is not None and not isinstance(raw, bool) else None
        )
    except (TypeError, ValueError):
        annotated = None
    if annotated is not None and (not math.isfinite(annotated) or annotated < 0.0):
        annotated = None
    return observed, annotated


def _finite_score(value: Any) -> float:
    """规范化 canonical 候选基础分数。"""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _mean(values: Sequence[float]) -> float:
    """计算有限数值均值。"""

    return round(sum(values) / len(values), 4) if values else 0.0


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    """计算确定性百分位。"""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


__all__ = [
    "DERIVED_INDEX_REASON_CODES",
    "DerivedMetadataBranchMetrics",
    "DerivedMetadataIndexSummary",
    "DerivedMetadataMatch",
    "DerivedMetadataReport",
    "RunLocalDerivedMetadataIndex",
    "run_derived_metadata_ablation",
]
