"""为自适应记忆注入提供确定性的效用选择。"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from ...memory.domain.memory_atom import (
    has_user_source_evidence,
    is_resolved_source_reference,
)
from ..domain.models import InjectionDecision, PresetName
from .presets import PRESETS

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_VALID_FACETS = frozenset({"entity", "role", "time", "event", "focus", "relation"})
_USER_SOURCE_ROLES = frozenset({"user"})
_SELECTION_METADATA_KEYS = (
    "importance",
    "intent_match",
    "temporal_value",
    "source_value",
    "create_time",
    "last_access_time",
    "interaction_type",
)
# 这些维度由原始正文与查询共同产生，正文重建后无法逐事实归属。
_QUERY_DERIVED_METADATA_KEYS = frozenset(
    {"intent_match", "temporal_value", "source_value"}
)


def _retained_user_facts(
    metadata: Any, allowed_source_roles: frozenset[str]
) -> list[str] | None:
    """返回可归属到用户来源的保留事实；无有效逐事实证据时返回 ``None``。

    每个 ``key_facts`` 条目都必须有一条服务端解析过的完整证据组：属于用户
    来源的保留，其余只有在整组仍可解析时才允许被丢弃；任何缺失、错位或
    无法解析的组都让整个候选 fail closed。
    """
    if len(allowed_source_roles) != 1 or "user" not in allowed_source_roles:
        return None
    if not isinstance(metadata, dict):
        return None
    facts = metadata.get("key_facts")
    evidence = metadata.get("fact_source_evidence")
    if (
        not isinstance(facts, list)
        or not facts
        or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
        or not isinstance(evidence, list)
        or len(facts) != len(evidence)
        or any(not isinstance(refs, (list, tuple)) for refs in evidence)
    ):
        return None
    retained: list[str] = []
    for fact, refs in zip(facts, evidence):
        if has_user_source_evidence(refs):
            retained.append(fact)
        elif not refs or not all(is_resolved_source_reference(ref) for ref in refs):
            return None
    return retained or None


def _content_is_user_attributable(
    metadata: dict[str, Any], retained: list[str]
) -> bool:
    """判断候选的完整正文是否仍可逐事实归属到用户来源。

    只有所有事实组都有用户证据、且摘要级 ``source_evidence`` 本身也是完整
    用户证据时，原正文与聚合分数才能原样保留。
    """
    return len(retained) == len(metadata["key_facts"]) and has_user_source_evidence(
        metadata.get("source_evidence")
    )


def metadata_has_user_evidence(
    metadata: Any, allowed_source_roles: frozenset[str] = _USER_SOURCE_ROLES
) -> bool:
    """判断候选能否在保留原正文与分数不变的前提下进入注入。

    要求每个事实组都有逐事实用户证据，且摘要级证据同样归属用户；混合候选
    虽然仍由 ``select_candidates`` 重建，但不能带着原聚合分数占用去重、
    排序与 top-K 槽位。默认角色策略与 ``InjectionExecutionContext`` 一致：
    模型注入只信任 user 消息。
    """
    retained = _retained_user_facts(metadata, allowed_source_roles)
    if retained is None or not isinstance(metadata, dict):
        return False
    return _content_is_user_attributable(metadata, retained)


def _user_supported_candidate(
    memory: dict[str, Any], allowed_source_roles: frozenset[str]
) -> dict[str, Any] | None:
    """在任何评分与预算计算前重建有逐事实用户证据的候选副本。"""
    metadata = memory.get("metadata")
    if not isinstance(metadata, dict):
        return None
    retained = _retained_user_facts(metadata, allowed_source_roles)
    if retained is None:
        return None
    summary_supported = _content_is_user_attributable(metadata, retained)
    content = memory.get("content") if summary_supported else None
    if not isinstance(content, str) or not content.strip():
        content = "；".join(retained)
    # 证据、身份和边界只用于前置判定，不进入 formatter 的输入。
    safe_metadata = {
        key: metadata[key]
        for key in _SELECTION_METADATA_KEYS
        if key in metadata
        and (summary_supported or key not in _QUERY_DERIVED_METADATA_KEYS)
    }
    safe_metadata["key_facts"] = retained
    if summary_supported:
        for key in ("topics", "participants", "identity_reference_lines"):
            values = metadata.get(key)
            if isinstance(values, list) and all(isinstance(v, str) for v in values):
                safe_metadata[key] = list(values)
        from .memory_formatter import _safe_projection_objects

        projections = _safe_projection_objects(metadata)
        if projections:
            safe_metadata["derived_projections"] = projections
    safe = {
        key: memory[key]
        for key in (
            "id",
            "doc_id",
            "memory_id",
            "score",
            "timestamp",
            "_matched_facets",
        )
        if key in memory
    }
    if not summary_supported:
        # 正文已由保留事实重建：原聚合分数与 facet 命中由被拒事实共同产生，
        # 不能逐事实归属，一律归零，避免其继续影响排序和必需维度覆盖。
        safe["score"] = 0.0
        safe.pop("_matched_facets", None)
    safe.update(content=content, metadata=safe_metadata)
    return safe


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
    allowed_source_roles: frozenset[str],
    required_facets: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        candidate = _user_supported_candidate(memory, allowed_source_roles)
        if candidate is None:
            rejected.append(memory)
        else:
            candidates.append(candidate)
    if (
        decision.resolved_preset is PresetName.TOOL_FIRST
        or decision.memory_budget_chars <= 0
        or decision.max_memories <= 0
    ):
        return [], rejected + candidates
    if not candidates:
        return [], rejected
    safe_facets = tuple(dict.fromkeys(f for f in required_facets if f in _VALID_FACETS))
    remaining = normalize_relevance(candidates)
    selected = select_by_utility(decision, remaining, required_facets=safe_facets)
    return selected, rejected + dropped_candidates(candidates, selected)


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
