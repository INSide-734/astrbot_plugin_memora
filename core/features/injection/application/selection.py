"""为自适应记忆注入提供确定性的效用选择。"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from ..domain.models import InjectionDecision, PresetName
from .presets import PRESETS

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_VALID_FACETS = frozenset({"entity", "role", "time", "event", "focus", "relation"})


def bounded_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, min(1.0, parsed))


def candidate_utility(
    memory: dict[str, Any],
    *,
    intent_match: float,
    temporal_value: float,
    source_value: float,
    redundancy: float,
    cost_penalty: float,
) -> float:
    """按照设计契约返回固定且确定的效用分数。"""

    metadata = memory.get("metadata") or {}
    relevance = bounded_float(
        memory.get("normalized_relevance", memory.get("score", 0.0))
    )
    importance = bounded_float(metadata.get("importance", 0.5), default=0.5)
    return (
        0.50 * relevance
        + 0.15 * intent_match
        + 0.15 * importance
        + 0.10 * temporal_value
        + 0.10 * source_value
        - 0.25 * redundancy
        - cost_penalty
    )


def select_candidates(
    decision: InjectionDecision,
    memories: Iterable[dict[str, Any]],
    *,
    required_facets: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [memory for memory in memories if isinstance(memory, dict)]
    if (
        decision.resolved_preset is PresetName.TOOL_FIRST
        or decision.memory_budget_chars <= 0
        or decision.max_memories <= 0
    ):
        return [], candidates
    if not candidates:
        return [], []
    safe_facets = tuple(dict.fromkeys(f for f in required_facets if f in _VALID_FACETS))
    remaining = normalize_relevance(candidates)
    selected = select_by_utility(decision, remaining, required_facets=safe_facets)
    return selected, dropped_candidates(candidates, selected)


def normalize_relevance(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_scores = [raw_score(memory) for memory in candidates]
    low = min(raw_scores)
    high = max(raw_scores)
    normalized: list[dict[str, Any]] = []
    for memory, score in zip(candidates, raw_scores):
        copy = dict(memory)
        copy["normalized_relevance"] = normalized_score(score, low, high)
        normalized.append(copy)
    return normalized


def normalized_score(score: float, low: float, high: float) -> float:
    if high == low:
        return 1.0 if high > 0.0 else 0.0
    return (score - low) / (high - low)


def select_by_utility(
    decision: InjectionDecision,
    remaining: list[dict[str, Any]],
    *,
    required_facets: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    preset = PRESETS[decision.resolved_preset]
    selected: list[dict[str, Any]] = []
    estimated_chars = 0
    covered_facets: set[str] = set()
    while remaining and len(selected) < decision.max_memories:
        ranked = ranked_candidates(
            decision,
            preset.cost_penalty_weight,
            remaining,
            selected,
            required_facets=required_facets,
            covered_facets=covered_facets,
        )
        negative_utility, _, chosen_index, chosen = ranked[0]
        utility = -negative_utility
        estimate = estimate_candidate_chars(decision, chosen)
        matched = chosen.get("_matched_facets")
        fills_required_gap = isinstance(matched, dict) and any(
            facet not in covered_facets
            and isinstance(matched.get(facet), (int, float))
            and matched[facet] > 0
            for facet in required_facets
        )
        if utility < preset.minimum_utility and not fills_required_gap:
            break
        remaining.pop(chosen_index)
        if estimated_chars + estimate > decision.memory_budget_chars:
            continue
        selected.append(chosen)
        estimated_chars += estimate
        # 记录新覆盖的维度，避免后续候选重复获得覆盖优先级。
        if isinstance(matched, dict) and required_facets:
            for facet in required_facets:
                val = matched.get(facet)
                if isinstance(val, (int, float)) and val > 0:
                    covered_facets.add(facet)
    return selected


def ranked_candidates(
    decision: InjectionDecision,
    cost_penalty_weight: float,
    remaining: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    *,
    required_facets: tuple[str, ...] = (),
    covered_facets: set[str] | None = None,
) -> list[tuple[float, str, int, dict[str, Any]]]:
    """对候选排序，并在存在缺失维度时优先选择可补足维度的候选。"""

    ranked: list[tuple[float, str, int, dict[str, Any]]] = []
    covering: list[tuple[float, str, int, dict[str, Any]]] = []
    uncovered = set(required_facets) - (covered_facets or set())
    total_required = len(required_facets)
    for index, memory in enumerate(remaining):
        estimate = estimate_candidate_chars(decision, memory)
        redundancy = max(
            (jaccard(memory, prior) for prior in selected),
            default=0.0,
        )
        metadata = memory.get("metadata") or {}
        utility = candidate_utility(
            memory,
            intent_match=bounded_float(metadata.get("intent_match", 0.0)),
            temporal_value=bounded_float(metadata.get("temporal_value", 0.0)),
            source_value=bounded_float(metadata.get("source_value", 0.0)),
            redundancy=redundancy,
            cost_penalty=(
                cost_penalty_weight * estimate / max(1, decision.memory_budget_chars)
            ),
        )
        newly_covered = 0
        # 覆盖奖励最多为 0.08，只奖励此前尚未覆盖的必需维度。
        if uncovered and total_required > 0:
            matched = memory.get("_matched_facets")
            if isinstance(matched, dict):
                newly_covered = sum(
                    1
                    for f in uncovered
                    if isinstance(matched.get(f), (int, float)) and matched[f] > 0
                )
                if newly_covered > 0:
                    utility += 0.08 * newly_covered / total_required
        row = (-utility, stable_memory_id(memory), index, memory)
        ranked.append(row)
        if newly_covered > 0:
            covering.append(row)
    selected_pool = covering or ranked
    selected_pool.sort(key=lambda item: (item[0], item[1], item[2]))
    return selected_pool


def dropped_candidates(
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_keys = [stable_memory_id(memory) for memory in selected]
    unmatched_keys = list(selected_keys)
    dropped = []
    for original in candidates:
        key = stable_memory_id(original)
        if key in unmatched_keys:
            unmatched_keys.remove(key)
        else:
            dropped.append(original)
    return dropped


def raw_score(memory: dict[str, Any]) -> float:
    try:
        score = float(memory.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return score if math.isfinite(score) else 0.0


def stable_memory_id(memory: dict[str, Any]) -> str:
    for key in ("id", "doc_id", "memory_id"):
        value = memory.get(key)
        if isinstance(value, (str, int)):
            return str(value)
    return str(memory.get("content", ""))


def memory_tokens(memory: dict[str, Any]) -> set[str]:
    return set(_TOKEN_RE.findall(str(memory.get("content", "")).casefold()))


def jaccard(first: dict[str, Any], second: dict[str, Any]) -> float:
    first_tokens = memory_tokens(first)
    second_tokens = memory_tokens(second)
    union = first_tokens | second_tokens
    if not union:
        return 0.0
    return len(first_tokens & second_tokens) / len(union)


def estimate_candidate_chars(
    decision: InjectionDecision,
    memory: dict[str, Any],
) -> int:
    content = str(memory.get("content", "") or "")
    content_limit = (
        decision.memory_max_chars if decision.memory_max_chars > 0 else len(content)
    )
    metadata_limit = (
        decision.metadata_max_chars if decision.metadata_max_chars > 0 else 180
    )
    return min(len(content), content_limit) + min(metadata_limit, 180)
