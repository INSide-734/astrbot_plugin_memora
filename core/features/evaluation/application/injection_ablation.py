"""候选到最终注入的离线观察与类别聚合。

模块导入保持无 AstrBot 依赖；生产 ``select_candidates`` 与来源资格判定由
宿主侧以可调用对象注入。逐用例只保留数值、计数与闭集枚举。
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "InjectionAblationObserver",
    "InjectionObservation",
    "aggregate_injection",
    "build_category_breakdown",
]

_FACETS = ("entity", "role", "time", "event", "focus", "relation")
_NEGATIVE_MARKER = "__no_relevant__"


@dataclass(slots=True)
class InjectionObservation:
    """单用例候选层与最终注入层的安全指标。"""

    candidate_count: int
    selected_count: int
    rejected_count: int
    source_rejected_count: int | None
    selection_dropped_count: int | None
    candidate_hit: bool
    final_injected_hit: bool
    injection_precision: float | None
    negative_injection: bool | None
    required_fact_coverage: float | None
    dropped_relevant: int
    budget_utilization: float | None
    resolved_preset: str
    decision_reason_codes: tuple[str, ...]
    route_signals: dict[str, Any]
    injection_simulation_ms: float

    def to_payload(self) -> dict[str, Any]:
        """返回可进入报告的 JSON 安全载荷。"""

        return {
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "rejected_count": self.rejected_count,
            "source_rejected_count": self.source_rejected_count,
            "selection_dropped_count": self.selection_dropped_count,
            "candidate_hit": self.candidate_hit,
            "final_injected_hit": self.final_injected_hit,
            "injection_precision": self.injection_precision,
            "negative_injection": self.negative_injection,
            "required_fact_coverage": self.required_fact_coverage,
            "dropped_relevant": self.dropped_relevant,
            "budget_utilization": self.budget_utilization,
            "resolved_preset": self.resolved_preset,
            "decision_reason_codes": list(self.decision_reason_codes),
            "route_signals": dict(self.route_signals),
            "injection_simulation_ms": self.injection_simulation_ms,
        }


class InjectionAblationObserver:
    """使用生产选择函数逐用例模拟最终注入。"""

    def __init__(
        self,
        *,
        decision: Any,
        select_candidates: Callable[..., Any],
        source_eligible: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        self._decision = decision
        self._select_candidates = select_candidates
        self._source_eligible = source_eligible

    def observe(self, case: Any, documents: Sequence[Any]) -> InjectionObservation:
        """返回候选命中、最终注入与来源/预算丢弃的逐用例指标。"""

        candidates = [_normalize_candidate(item) for item in documents]
        metadata = getattr(case, "metadata", None) or {}
        relevant_ids = _relevant_ids(getattr(case, "relevant_doc_ids", ()))
        candidate_ids = {item["doc_id"] for item in candidates}
        required_facets = _required_facets(metadata)

        started = time.perf_counter()
        selected, rejected = self._select(required_facets, candidates)
        simulation_ms = round((time.perf_counter() - started) * 1000.0, 4)

        selected_ids = {_candidate_id(item) for item in selected if _candidate_id(item)}
        source_rejected = self._source_rejected(candidates)
        rejected_count = len(rejected)
        selection_dropped = (
            None
            if source_rejected is None
            else max(0, rejected_count - source_rejected)
        )
        is_negative = _is_negative(metadata, relevant_ids)
        budget = _positive_number(getattr(self._decision, "memory_budget_chars", 0))
        selected_chars = sum(len(str(item.get("content", ""))) for item in selected)

        return InjectionObservation(
            candidate_count=len(candidates),
            selected_count=len(selected),
            rejected_count=rejected_count,
            source_rejected_count=source_rejected,
            selection_dropped_count=selection_dropped,
            candidate_hit=bool(candidate_ids & relevant_ids),
            final_injected_hit=bool(selected_ids & relevant_ids),
            injection_precision=_precision(selected_ids, relevant_ids),
            negative_injection=bool(selected_ids) if is_negative else None,
            required_fact_coverage=_required_fact_coverage(
                metadata, selected_ids, selected
            ),
            dropped_relevant=len((candidate_ids & relevant_ids) - selected_ids),
            budget_utilization=(
                round(min(1.0, selected_chars / budget), 4) if budget else None
            ),
            resolved_preset=_preset_name(
                getattr(self._decision, "resolved_preset", "")
            ),
            decision_reason_codes=tuple(
                str(code)
                for code in (getattr(self._decision, "reason_codes", ()) or ())
            ),
            route_signals=_route_signals(candidates),
            injection_simulation_ms=simulation_ms,
        )

    def _select(
        self,
        required_facets: tuple[str, ...],
        candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """调用生产选择函数；保持来源角色与必需维度契约。"""

        selected, rejected = self._select_candidates(
            self._decision,
            candidates,
            allowed_source_roles=frozenset({"user"}),
            required_facets=required_facets,
        )
        return list(selected or []), list(rejected or [])

    def _source_rejected(self, candidates: list[dict[str, Any]]) -> int | None:
        """使用生产来源判定统计来源门拒绝；不可用时保持 None。"""

        eligible = self._source_eligible
        if eligible is None:
            try:
                from ...injection.application.selection import (
                    metadata_has_user_evidence,
                )

                eligible = metadata_has_user_evidence
            except ImportError:
                return None
        try:
            rejected = sum(
                1
                for candidate in candidates
                if not eligible(candidate.get("metadata") or {})
            )
        except Exception:
            return None
        return rejected


def aggregate_injection(
    observations: Sequence[Mapping[str, Any] | None],
) -> dict[str, Any]:
    """聚合逐用例注入指标；缺失分母保持 None。"""

    items = [item for item in observations if isinstance(item, Mapping)]
    if not items:
        return {}
    negative = [item for item in items if item.get("negative_injection") is not None]
    precision = [
        float(item["injection_precision"])
        for item in items
        if item.get("injection_precision") is not None
    ]
    coverage = [
        float(item["required_fact_coverage"])
        for item in items
        if item.get("required_fact_coverage") is not None
    ]
    budget = [
        float(item["budget_utilization"])
        for item in items
        if item.get("budget_utilization") is not None
    ]
    simulation = [
        float(item["injection_simulation_ms"])
        for item in items
        if item.get("injection_simulation_ms") is not None
    ]
    return {
        "total_cases": len(items),
        "candidate_hit_rate": _bool_rate(items, "candidate_hit"),
        "final_injected_hit_rate": _bool_rate(items, "final_injected_hit"),
        "injection_precision": _mean_or_none(precision),
        "negative_injection_rate": _bool_rate(negative, "negative_injection"),
        "required_fact_coverage": _mean_or_none(coverage),
        "dropped_relevant": sum(int(item.get("dropped_relevant", 0)) for item in items),
        "budget_utilization": _mean_or_none(budget),
        "source_rejected_count": _sum_or_none(items, "source_rejected_count"),
        "selection_dropped_count": _sum_or_none(items, "selection_dropped_count"),
        "injection_simulation_p95_ms": _percentile(simulation, 95),
        "preset_counts": _enum_counts(items, "resolved_preset"),
        "reason_code_counts": _reason_counts(items),
    }


def build_category_breakdown(
    results: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    """按 case categories 聚合注入、质量与成本代理指标。"""

    groups: dict[str, list[Any]] = defaultdict(list)
    for result in results:
        metadata = getattr(result, "metadata", None) or {}
        for category in _categories(metadata):
            groups[category].append(result)
    breakdown: dict[str, dict[str, Any]] = {}
    for category, items in sorted(groups.items()):
        observations = [
            getattr(item, "injection", None)
            for item in items
            if isinstance(getattr(item, "injection", None), Mapping)
        ]
        summary = aggregate_injection(observations)
        summary["case_count"] = len(items)
        summary["observed_p95_latency_ms"] = _percentile(
            [
                float(getattr(item, "observed_latency_ms", 0.0))
                for item in items
                if getattr(item, "observed_latency_ms", None) is not None
            ],
            95,
        )
        summary["observed_provider_calls"] = _mean_or_none(
            [
                float(metrics["observed_provider_calls"])
                for metrics in (
                    getattr(item, "advanced_metrics", {}) or {} for item in items
                )
                if metrics.get("observed_provider_calls") is not None
            ]
        )
        breakdown[category] = summary
    return breakdown


def _normalize_candidate(item: Any) -> dict[str, Any]:
    """把 HybridResult/dict 收敛为选择函数需要的候选副本。"""

    if isinstance(item, Mapping):
        doc_id = _candidate_id(item)
        content = str(item.get("content", ""))
        score = _number(item.get("score", item.get("final_score", 0.0)))
        metadata = item.get("metadata")
        breakdown = item.get("score_breakdown")
    else:
        doc_id = _candidate_id(
            {
                "doc_id": getattr(item, "doc_id", None),
                "id": getattr(item, "id", None),
                "memory_id": getattr(item, "memory_id", None),
            }
        )
        content = str(getattr(item, "content", ""))
        score = _number(getattr(item, "final_score", 0.0))
        metadata = getattr(item, "metadata", None)
        breakdown = getattr(item, "score_breakdown", None)
    safe = {
        "doc_id": doc_id,
        "id": doc_id,
        "content": content,
        "score": score,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
        "score_breakdown": dict(breakdown) if isinstance(breakdown, Mapping) else {},
    }
    matched = _matched_facets(breakdown)
    if matched:
        safe["_matched_facets"] = matched
    return safe


def _matched_facets(breakdown: Any) -> dict[str, float]:
    if not isinstance(breakdown, Mapping):
        return {}
    matched: dict[str, float] = {}
    for facet in _FACETS:
        value = breakdown.get(facet)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            matched[facet] = float(value)
    return matched


def _route_signals(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """从候选 breakdown 提取无正文的原始路由信号。"""

    breakdowns: list[Mapping[str, Any]] = []
    for item in candidates:
        breakdown = item.get("score_breakdown")
        if isinstance(breakdown, Mapping):
            breakdowns.append(breakdown)
    intents = {
        str(breakdown.get("query_intent"))
        for breakdown in breakdowns
        if breakdown.get("query_intent")
    }
    return {
        "query_intent": next(iter(sorted(intents)), ""),
        "document_route_weight": _max_breakdown(breakdowns, "document_route_weight"),
        "graph_route_weight": _max_breakdown(breakdowns, "graph_route_weight"),
        "atom_route_weight": _max_breakdown(breakdowns, "atom_route_weight"),
        "document_route_score_max": _max_breakdown(breakdowns, "document_route_score"),
        "graph_route_score_max": _max_breakdown(breakdowns, "graph_route_score"),
        "atom_route_score_max": _max_breakdown(breakdowns, "atom_route_score"),
    }


def _max_breakdown(breakdowns: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [
        _number(breakdown[key])
        for breakdown in breakdowns
        if breakdown.get(key) is not None
    ]
    return max(values) if values else None


def _required_facets(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    raw = metadata.get("required_facets")
    if raw is None:
        plan = metadata.get("query_plan")
        if isinstance(plan, Mapping):
            raw = plan.get("required_facets")
    return tuple(
        str(facet).strip() for facet in (raw or ()) if str(facet).strip() in _FACETS
    )


def _required_fact_coverage(
    metadata: Mapping[str, Any],
    selected_ids: set[str],
    selected: Sequence[Mapping[str, Any]],
) -> float | None:
    required_ids = _string_list(metadata.get("required_fact_ids"))
    if required_ids:
        hits = sum(1 for item in required_ids if item in selected_ids)
        return round(hits / len(required_ids), 4)
    required_facts = _string_list(metadata.get("required_key_facts"))
    if not required_facts:
        return None
    text = " ".join(str(item.get("content", "")) for item in selected)
    normalized = _normalize_text(text)
    hits = sum(1 for fact in required_facts if _normalize_text(fact) in normalized)
    return round(hits / len(required_facts), 4)


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


def _relevant_ids(values: Any) -> set[str]:
    return {
        str(value).strip()
        for value in (values or ())
        if str(value).strip() and str(value).strip() != _NEGATIVE_MARKER
    }


def _is_negative(metadata: Mapping[str, Any], relevant_ids: set[str]) -> bool:
    if metadata.get("expected_no_hit") is True:
        return True
    raw = metadata.get("relevant_doc_ids")
    return not relevant_ids or (
        isinstance(raw, Sequence)
        and not isinstance(raw, (str, bytes))
        and _NEGATIVE_MARKER in {str(item) for item in raw}
    )


def _precision(selected_ids: set[str], relevant_ids: set[str]) -> float | None:
    if not selected_ids:
        return None
    return round(len(selected_ids & relevant_ids) / len(selected_ids), 4)


def _candidate_id(item: Mapping[str, Any]) -> str:
    value = (
        item.get("doc_id")
        or item.get("id")
        or item.get("memory_id")
        or item.get("document_id")
    )
    return str(value or "").strip()


def _preset_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def _positive_number(value: Any) -> float:
    number = _number(value)
    return number if number > 0.0 else 0.0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _bool_rate(items: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [item.get(key) for item in items if item.get(key) is not None]
    if not values:
        return None
    return round(sum(1 for value in values if value) / len(values), 4)


def _mean_or_none(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _sum_or_none(items: Sequence[Mapping[str, Any]], key: str) -> int | None:
    values = [item.get(key) for item in items if item.get(key) is not None]
    if not values:
        return None
    return sum(int(value) for value in values if value is not None)


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    index = (p / 100.0) * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return round(ordered[lower], 4)
    value = ordered[lower] * (upper - index) + ordered[upper] * (index - lower)
    return round(value, 4)


def _enum_counts(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        value = str(item.get(key) or "").strip()
        if value:
            counts[value] += 1
    return dict(sorted(counts.items()))


def _reason_counts(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        reasons = item.get("decision_reason_codes") or ()
        for reason in reasons:
            value = str(reason).strip()
            if value:
                counts[value] += 1
    return dict(sorted(counts.items()))
