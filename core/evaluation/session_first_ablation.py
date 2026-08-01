"""会话优先召回的隔离、只读反事实评测。"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .retrieval_quality import (
    EvaluationCase,
    make_memory_engine_retriever,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

SESSION_SCENARIOS = frozenset({"session_hit", "session_no_hit", "mixed"})
SIMPLE_INTENTS = frozenset(
    {"fact", "preference", "goal", "interest", "boundary", "health_preference"}
)
SESSION_REASON_CODES = frozenset(
    {
        "session_evidence_sufficient",
        "missing_trusted_session",
        "session_no_hit",
        "session_evidence_insufficient",
        "intent_requires_full_recall",
        "session_stage_failed",
        "equivalent_to_baseline",
        "readonly_snapshot_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class SessionFirstPreset:
    """固定的离线证据门阈值，不映射为生产配置。"""

    minimum_score: float = 0.75
    minimum_margin: float = 0.05

    def __post_init__(self) -> None:
        """拒绝非有限或越界阈值，保持实验结果可复现。"""

        if (
            not math.isfinite(self.minimum_score)
            or not 0.0 <= self.minimum_score <= 1.0
        ):
            raise ValueError("session_score_threshold_invalid")
        if (
            not math.isfinite(self.minimum_margin)
            or not 0.0 <= self.minimum_margin <= 1.0
        ):
            raise ValueError("session_margin_threshold_invalid")


@dataclass(frozen=True, slots=True)
class SessionFirstDecision:
    """单个用例的安全短路决定，仅保存标量和固定状态。"""

    decision: str
    reason_code: str
    candidate_count: int = 0
    score: float | None = None
    margin: float | None = None


@dataclass(frozen=True, slots=True)
class SessionFirstBranchMetrics:
    """单个召回分支的质量与延迟聚合。"""

    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    observed_p50_latency_ms: float | None
    observed_p95_latency_ms: float | None
    annotated_p50_latency_ms: float | None
    annotated_p95_latency_ms: float | None
    annotated_provider_calls: float | None
    annotated_token_cost: float | None


@dataclass(frozen=True, slots=True)
class SessionFirstReport:
    """会话优先离线实验的脱敏报告。"""

    status: str
    reason_code: str
    total_cases: int
    k: int
    baseline: SessionFirstBranchMetrics | None
    session: SessionFirstBranchMetrics | None
    effective: SessionFirstBranchMetrics | None
    reason_code_aggregates: dict[str, int] = field(default_factory=dict)
    scenario_breakdown: dict[str, dict[str, int | float]] = field(default_factory=dict)
    would_short_circuit: int = 0
    wrong_short_circuit: int = 0
    estimated_full_recall_savings: float = 0.0
    annotated_provider_calls: float | None = None
    annotated_token_cost: float | None = None
    effective_settings: dict[str, float | int] = field(default_factory=dict)


RetrieverFn = Callable[
    [EvaluationCase, int],
    Sequence[Any] | Awaitable[Sequence[Any]],
]


def make_session_first_retrievers(
    engine: Any,
    *,
    baseline_uses_session_filter: bool,
) -> tuple[RetrieverFn, RetrieverFn]:
    """从同一只读 engine 构造基线与精确 Session callable。"""

    engine_retriever = make_memory_engine_retriever(engine)

    async def baseline(case: EvaluationCase, k: int) -> Sequence[Any]:
        """按当前基线的 Session 过滤状态执行完整召回。"""

        metadata = dict(case.metadata)
        metadata.setdefault("query_intent", metadata.get("intent"))
        if not baseline_uses_session_filter:
            metadata.pop("session_id", None)
        return await engine_retriever(replace(case, metadata=metadata), k)

    async def session(case: EvaluationCase, k: int) -> Sequence[Any]:
        """保留可信 Session 并执行精确会话召回。"""

        metadata = dict(case.metadata)
        metadata.setdefault("query_intent", metadata.get("intent"))
        return await engine_retriever(replace(case, metadata=metadata), k)

    return baseline, session


async def run_session_first(
    cases: Sequence[EvaluationCase],
    baseline_retriever: RetrieverFn,
    session_retriever: RetrieverFn,
    *,
    k: int,
    preset: SessionFirstPreset | None = None,
    snapshot_available: bool = True,
) -> SessionFirstReport:
    """双跑基线和会话分支，普通会话失败回退，基线失败和取消异常传播。"""

    safe_k = max(1, min(int(k), 20))
    active_preset = preset or SessionFirstPreset()
    if not snapshot_available:
        return SessionFirstReport(
            status="skipped",
            reason_code="readonly_snapshot_unavailable",
            total_cases=len(cases),
            k=safe_k,
            baseline=None,
            session=None,
            effective=None,
            effective_settings=_settings(active_preset, safe_k),
        )

    baseline_rows: list[_BranchRow] = []
    session_rows: list[_BranchRow] = []
    decisions: list[SessionFirstDecision] = []
    scenario_stats: dict[str, Counter[str]] = defaultdict(Counter)

    for case in cases:
        session_started = time.perf_counter()
        session_error = False
        try:
            session_raw = await _resolve(session_retriever(case, safe_k))
        except asyncio.CancelledError:
            raise
        except Exception:
            session_raw = []
            session_error = True
        session_latencies = _latencies(case, session_started, branch="session")

        baseline_started = time.perf_counter()
        baseline_raw = await _resolve(baseline_retriever(case, safe_k))
        baseline_latencies = _latencies(case, baseline_started, branch="baseline")

        session_candidates = _normalize_candidates(session_raw)
        baseline_candidates = _normalize_candidates(baseline_raw)
        session_rows.append(
            _branch_row(case, session_candidates, session_latencies, "session")
        )
        baseline_rows.append(
            _branch_row(case, baseline_candidates, baseline_latencies, "baseline")
        )

        if session_error:
            decision = SessionFirstDecision("would_fallback", "session_stage_failed")
        else:
            decision = _decide(
                case, session_candidates, baseline_candidates, active_preset
            )
        decisions.append(decision)
        scenario = str(case.metadata.get("scenario") or "unknown")
        scenario_stats[scenario][decision.reason_code] += 1
        if decision.decision == "would_short_circuit":
            scenario_stats[scenario]["would_short_circuit"] += 1

    baseline_metrics = _branch_metrics(baseline_rows, safe_k)
    session_metrics = _branch_metrics(session_rows, safe_k)
    effective_rows = [
        session_row if decision.decision == "would_short_circuit" else baseline_row
        for session_row, baseline_row, decision in zip(
            session_rows,
            baseline_rows,
            decisions,
            strict=True,
        )
    ]
    effective_metrics = _branch_metrics(effective_rows, safe_k)
    reason_counts = Counter(item.reason_code for item in decisions)
    short_circuits = reason_counts.get("session_evidence_sufficient", 0)
    wrong_short_circuits = _count_wrong_short_circuits(cases, session_rows, decisions)
    status = "completed"
    reason_code = "available"
    if decisions and all(
        item.reason_code == "equivalent_to_baseline" for item in decisions
    ):
        status = "skipped"
        reason_code = "equivalent_to_baseline"
    return SessionFirstReport(
        status=status,
        reason_code=reason_code,
        total_cases=len(cases),
        k=safe_k,
        baseline=baseline_metrics,
        session=session_metrics,
        effective=effective_metrics,
        reason_code_aggregates=dict(sorted(reason_counts.items())),
        scenario_breakdown={
            name: dict(sorted(counts.items()))
            for name, counts in sorted(scenario_stats.items())
        },
        would_short_circuit=short_circuits,
        wrong_short_circuit=wrong_short_circuits,
        estimated_full_recall_savings=(short_circuits / len(cases)) if cases else 0.0,
        annotated_provider_calls=effective_metrics.annotated_provider_calls,
        annotated_token_cost=effective_metrics.annotated_token_cost,
        effective_settings=_settings(active_preset, safe_k),
    )


def load_session_first_cases(path: str | Path) -> list[EvaluationCase]:
    """加载并校验匿名会话优先场景，拒绝未知场景和危险字段。"""

    cases: list[EvaluationCase] = []
    source = Path(path)
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"session_fixture_json_invalid:{line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"session_fixture_object_required:{line_number}")
        case_id = str(payload.get("case_id") or "").strip()
        query = str(payload.get("query") or "").strip()
        relevant = payload.get("relevant_doc_ids")
        metadata = payload.get("metadata")
        if not case_id or not query or not isinstance(relevant, list) or not relevant:
            raise ValueError(f"session_fixture_required_field:{line_number}")
        if not isinstance(metadata, dict):
            raise ValueError(f"session_fixture_metadata_required:{line_number}")
        scenario = str(metadata.get("scenario") or "")
        if scenario not in SESSION_SCENARIOS:
            raise ValueError(f"session_fixture_scenario_invalid:{line_number}")
        if any(not isinstance(item, str) or not item.strip() for item in relevant):
            raise ValueError(f"session_fixture_doc_id_invalid:{line_number}")
        critical = metadata.get("critical_long_term_doc_ids", [])
        if not isinstance(critical, list) or any(
            item not in relevant for item in critical
        ):
            raise ValueError(f"session_fixture_critical_ids_invalid:{line_number}")
        if scenario == "mixed" and not critical:
            raise ValueError(f"session_fixture_critical_ids_invalid:{line_number}")
        cases.append(
            EvaluationCase(
                case_id=case_id,
                query=query,
                relevant_doc_ids={item.strip() for item in relevant},
                metadata=dict(metadata),
            )
        )
    return cases


@dataclass(frozen=True, slots=True)
class _Candidate:
    """进程内使用的最小候选结构，不进入报告。"""

    doc_id: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _BranchRow:
    """进程内使用的单分支评测行。"""

    case: EvaluationCase
    candidates: list[_Candidate]
    observed_latency_ms: float
    annotated_latency_ms: float | None
    annotated_provider_calls: float | None
    annotated_token_cost: float | None


async def _resolve(value: Sequence[Any] | Awaitable[Sequence[Any]]) -> Sequence[Any]:
    """兼容同步和异步检索 callable。"""

    return await value if inspect.isawaitable(value) else value


def _normalize_candidates(items: Sequence[Any]) -> list[_Candidate]:
    """将检索结果转换为有限分数和匿名文档标识。"""

    normalized: list[_Candidate] = []
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
            finite_score = float(score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(finite_score):
            continue
        normalized.append(
            _Candidate(
                doc_id=str(doc_id or "").strip(),
                score=finite_score,
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        )
    return [item for item in normalized if item.doc_id]


def _decide(
    case: EvaluationCase,
    session_candidates: list[_Candidate],
    baseline_candidates: list[_Candidate],
    preset: SessionFirstPreset,
) -> SessionFirstDecision:
    """执行 fail-closed 会话证据门。"""

    metadata = case.metadata or {}
    if (
        not bool(metadata.get("trusted_session"))
        or not str(metadata.get("session_id") or "").strip()
    ):
        return SessionFirstDecision("would_fallback", "missing_trusted_session")
    intent = str(metadata.get("intent") or metadata.get("query_intent") or "").lower()
    if intent not in SIMPLE_INTENTS:
        return SessionFirstDecision("would_fallback", "intent_requires_full_recall")
    if not session_candidates:
        return SessionFirstDecision("would_fallback", "session_no_hit")
    ordered = sorted(session_candidates, key=lambda item: item.score, reverse=True)
    if str(metadata.get("scenario") or "") == "mixed" and not _critical_ids(case):
        return SessionFirstDecision(
            "would_fallback",
            "session_evidence_insufficient",
            len(ordered),
        )
    top = ordered[0]
    margin = top.score - ordered[1].score if len(ordered) > 1 else 1.0
    if top.score < preset.minimum_score or margin < preset.minimum_margin:
        return SessionFirstDecision(
            "would_fallback",
            "session_evidence_insufficient",
            len(ordered),
            top.score,
            margin,
        )
    if not _candidates_authorized(case, ordered) or not _critical_ids(case).issubset(
        {item.doc_id for item in ordered}
    ):
        return SessionFirstDecision(
            "would_fallback",
            "session_evidence_insufficient",
            len(ordered),
            top.score,
            margin,
        )
    baseline_ids = [item.doc_id for item in baseline_candidates]
    if [item.doc_id for item in ordered] == baseline_ids:
        return SessionFirstDecision(
            "would_fallback",
            "equivalent_to_baseline",
            len(ordered),
            top.score,
            margin,
        )
    return SessionFirstDecision(
        "would_short_circuit",
        "session_evidence_sufficient",
        len(ordered),
        top.score,
        margin,
    )


def _candidates_authorized(
    case: EvaluationCase, candidates: Sequence[_Candidate]
) -> bool:
    """校验 Session、作用域、隐私、角色、revision、validity 和冲突状态。"""

    expected_keys = (
        "session_id",
        "scope",
        "privacy_level",
        "role",
        "source_revision",
        "reference_time",
    )
    metadata = case.metadata or {}
    if str(metadata.get("conflict_mode") or "").lower() == "unresolved":
        return False
    for candidate in candidates:
        if candidate.metadata.get("canonical", True) is False:
            return False
        if candidate.metadata.get("is_canonical", True) is False:
            return False
        if candidate.metadata.get("valid") is False:
            return False
        if str(candidate.metadata.get("validity") or "").lower() in {
            "expired",
            "invalid",
            "stale",
        }:
            return False
        if str(candidate.metadata.get("conflict_status") or "").lower() in {
            "unresolved",
            "ambiguous",
        }:
            return False
        for key in expected_keys:
            expected = metadata.get(key)
            if expected is not None and candidate.metadata.get(key) != expected:
                return False
        if metadata.get("source_revision_required") and not candidate.metadata.get(
            "source_revision"
        ):
            return False
    return True


def _critical_ids(case: EvaluationCase) -> set[str]:
    """读取只用于进程内安全门的关键长期文档集合。"""

    values = case.metadata.get("critical_long_term_doc_ids", [])
    return {str(item).strip() for item in values if str(item).strip()}


def _count_wrong_short_circuits(
    cases: Sequence[EvaluationCase],
    session_rows: Sequence[_BranchRow],
    decisions: Sequence[SessionFirstDecision],
) -> int:
    """统计会话结果遗漏标注关键长期事实的错误短路。"""

    wrong = 0
    for case, row, decision in zip(cases, session_rows, decisions, strict=True):
        if decision.decision != "would_short_circuit":
            continue
        if not _critical_ids(case).issubset({item.doc_id for item in row.candidates}):
            wrong += 1
    return wrong


def _branch_metrics(rows: Sequence[_BranchRow], k: int) -> SessionFirstBranchMetrics:
    """使用现有 Recall@K、MRR、nDCG 定义聚合分支质量。"""

    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    observed_latencies: list[float] = []
    annotated_latencies: list[float] = []
    for row in rows:
        case = row.case
        ranked = [
            item.doc_id
            for item in sorted(
                row.candidates,
                key=lambda item: item.score,
                reverse=True,
            )
        ]
        if case.metadata.get("expected_no_hit") is True:
            score = 1.0 if not ranked[:k] else 0.0
            recalls.append(score)
            mrrs.append(score)
            ndcgs.append(score)
        else:
            recalls.append(recall_at_k(ranked, case.relevant_doc_ids, k=k))
            mrrs.append(reciprocal_rank(ranked, case.relevant_doc_ids))
            ndcgs.append(ndcg_at_k(ranked, case.relevant_doc_ids, k=k))
        observed_latencies.append(row.observed_latency_ms)
        if row.annotated_latency_ms is not None:
            annotated_latencies.append(row.annotated_latency_ms)
    annotated_provider_calls = [
        item.annotated_provider_calls
        for item in rows
        if item.annotated_provider_calls is not None
    ]
    annotated_token_cost = [
        item.annotated_token_cost
        for item in rows
        if item.annotated_token_cost is not None
    ]
    return SessionFirstBranchMetrics(
        recall_at_k=_mean(recalls),
        mrr=_mean(mrrs),
        ndcg_at_k=_mean(ndcgs),
        observed_p50_latency_ms=_percentile(observed_latencies, 50),
        observed_p95_latency_ms=_percentile(observed_latencies, 95),
        annotated_p50_latency_ms=_percentile(annotated_latencies, 50),
        annotated_p95_latency_ms=_percentile(annotated_latencies, 95),
        annotated_provider_calls=(
            round(sum(annotated_provider_calls), 4)
            if annotated_provider_calls
            else None
        ),
        annotated_token_cost=(
            round(sum(annotated_token_cost), 4) if annotated_token_cost else None
        ),
    )


def _branch_row(
    case: EvaluationCase,
    candidates: list[_Candidate],
    latencies: tuple[float, float | None],
    branch: str,
) -> _BranchRow:
    """把实测延迟与匿名 fixture 标注成本分列到内部评测行。"""

    return _BranchRow(
        case=case,
        candidates=candidates,
        observed_latency_ms=latencies[0],
        annotated_latency_ms=latencies[1],
        annotated_provider_calls=_optional_non_negative_number(
            case.metadata.get(f"annotated_{branch}_provider_calls")
        ),
        annotated_token_cost=_optional_non_negative_number(
            case.metadata.get(f"annotated_{branch}_token_cost")
        ),
    )


def _latencies(
    case: EvaluationCase, started: float, *, branch: str
) -> tuple[float, float | None]:
    """返回墙钟实测延迟和可选的分支人工标注延迟。"""

    observed = max(0.0, (time.perf_counter() - started) * 1000)
    annotated = _optional_non_negative_number(
        case.metadata.get(f"annotated_{branch}_latency_ms")
    )
    return observed, annotated


def _optional_non_negative_number(value: Any) -> float | None:
    """把可选 fixture 标注规范化为非负有限数值。"""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0.0 else None


def _mean(values: Sequence[float]) -> float:
    """计算有限数值均值。"""

    return round(sum(values) / len(values), 4) if values else 0.0


def _percentile(values: Sequence[float], percentile: int) -> float | None:
    """计算确定性线性百分位，空输入返回 None。"""

    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def _settings(preset: SessionFirstPreset, k: int) -> dict[str, float | int]:
    """返回可安全展示的实验设置。"""

    return {
        "minimum_score": round(preset.minimum_score, 4),
        "minimum_margin": round(preset.minimum_margin, 4),
        "k": k,
    }


__all__ = [
    "SESSION_REASON_CODES",
    "SESSION_SCENARIOS",
    "SIMPLE_INTENTS",
    "SessionFirstBranchMetrics",
    "SessionFirstDecision",
    "SessionFirstPreset",
    "SessionFirstReport",
    "load_session_first_cases",
    "make_session_first_retrievers",
    "run_session_first",
]
