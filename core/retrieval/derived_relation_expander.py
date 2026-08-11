"""基于 active relation 执行有界的一跳 canonical evidence 扩展。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models.memory_evolution import (
    ExpansionBudget,
    MemorySourceRef,
    RelationView,
    ScopeContext,
)
from ..shared.adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
)
from ..shared.temporal import normalize_datetime, visible_at
from .rrf_fusion import HybridResult

_PRIVACY_ORDER = {"public": 0, "shared": 1, "confidential": 2}


class DerivedRelationExpander:
    """读取派生关系，但只返回仍满足当前访问边界的 canonical 记忆。"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.DERIVED_READER,
        native=frozenset({AdapterCapability.REFERENCE_TIME}),
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.SCORING,
                AdapterCapability.CANCELLATION,
            }
        ),
        score=ScoreSemantics(
            direction=ScoreDirection.HIGHER_IS_BETTER,
            minimum=0.0,
            maximum=1.0,
            normalization=NormalizationScope.CALLER,
        ),
    )

    def __init__(
        self,
        reader: Any,
        *,
        per_seed_limit: int = 2,
        global_limit: int = 8,
        decay_half_life_days: float = 180.0,
    ) -> None:
        self.reader = reader
        self.per_seed_limit = max(0, int(per_seed_limit))
        self.global_limit = max(0, int(global_limit))
        self.decay_half_life_days = max(1.0, float(decay_half_life_days))

    async def expand(
        self,
        seeds: list[HybridResult],
        *,
        scope: ScopeContext,
        budget: ExpansionBudget,
        reference_time: datetime | None = None,
    ) -> list[HybridResult]:
        """保留直接结果，并在所有本地校验通过后追加最多一跳的目标记忆。"""

        direct = _deduplicate_direct(seeds)
        if not direct or self.per_seed_limit == 0 or self.global_limit == 0:
            return direct

        try:
            as_of = normalize_datetime(reference_time) or datetime.now(timezone.utc)
            proposals = await self._collect_proposals(direct, scope, as_of)
            if not proposals:
                return direct
            target_ids = tuple(dict.fromkeys(item[2] for item in proposals))
            sources = await self.reader.load_sources(target_ids)
            sources_by_id = {source.memory_id: source for source in sources}
            return self._merge_candidates(
                direct, proposals, sources_by_id, scope, budget, as_of
            )
        except Exception:
            return direct

    async def _collect_proposals(
        self,
        seeds: list[HybridResult],
        scope: ScopeContext,
        reference_time: datetime,
    ) -> list[tuple[HybridResult, RelationView, int, str]]:
        proposals: list[tuple[HybridResult, RelationView, int, str]] = []
        for seed in seeds:
            relations = await self.reader.active_relations_for_seeds(
                (seed.doc_id,),
                scope.scope_key,
                self.per_seed_limit,
            )
            for relation in relations[: self.per_seed_limit]:
                endpoint = _opposite_endpoint(relation, seed.doc_id)
                if endpoint is None:
                    continue
                target_id, expected_revision = endpoint
                if (
                    relation.scope_key != scope.scope_key
                    or not _privacy_allowed(relation.privacy_level, scope.privacy_level)
                    or not _relation_is_current(relation, reference_time)
                    or not expected_revision
                ):
                    continue
                proposals.append((seed, relation, target_id, expected_revision))
        return proposals

    def _merge_candidates(
        self,
        direct: list[HybridResult],
        proposals: list[tuple[HybridResult, RelationView, int, str]],
        sources_by_id: dict[int, MemorySourceRef],
        scope: ScopeContext,
        budget: ExpansionBudget,
        reference_time: datetime,
    ) -> list[HybridResult]:
        direct_by_id = {item.doc_id: item for item in direct}
        derived_by_id: dict[int, HybridResult] = {}
        now = reference_time

        for seed, relation, target_id, expected_revision in proposals:
            target = sources_by_id.get(target_id)
            if not _source_is_current(target, expected_revision, scope, now):
                continue
            derived_score = min(
                1.0,
                seed.final_score
                * relation.confidence
                * _time_decay(target.occurred_at, now, self.decay_half_life_days),
            )
            direct_match = direct_by_id.get(target_id)
            if direct_match is not None:
                direct_match.final_score = max(direct_match.final_score, derived_score)
                continue
            current = derived_by_id.get(target_id)
            if current is not None and current.final_score >= derived_score:
                continue
            content = target.content or ""
            if not content:
                continue
            derived_by_id[target_id] = HybridResult(
                doc_id=target_id,
                final_score=derived_score,
                rrf_score=derived_score,
                bm25_score=None,
                vector_score=None,
                content=content,
                metadata={
                    "scope_key": target.scope_key,
                    "privacy_level": target.privacy_level,
                    "occurred_at": target.occurred_at.isoformat(),
                    "derived": True,
                    "derived_relation_type": relation.relation_type.value,
                },
                score_breakdown={
                    "derived_relation_score": round(derived_score, 4),
                    "derived_relation_confidence": round(relation.confidence, 4),
                },
            )

        remaining_items = max(0, budget.max_items - len(direct))
        remaining_chars = max(
            0,
            budget.max_chars - sum(len(item.content) for item in direct),
        )
        expansion_limit = min(self.global_limit, remaining_items)
        accepted: list[HybridResult] = []
        for candidate in sorted(
            derived_by_id.values(),
            key=lambda item: (-item.final_score, item.doc_id),
        ):
            if len(accepted) >= expansion_limit:
                break
            content_chars = len(candidate.content)
            if content_chars > remaining_chars:
                continue
            accepted.append(candidate)
            remaining_chars -= content_chars
        return [*direct, *accepted]


def _deduplicate_direct(seeds: list[HybridResult]) -> list[HybridResult]:
    direct: list[HybridResult] = []
    by_id: dict[int, HybridResult] = {}
    for seed in seeds:
        existing = by_id.get(seed.doc_id)
        if existing is not None:
            existing.final_score = max(existing.final_score, seed.final_score)
            continue
        copied = HybridResult(
            doc_id=seed.doc_id,
            final_score=seed.final_score,
            rrf_score=seed.rrf_score,
            bm25_score=seed.bm25_score,
            vector_score=seed.vector_score,
            content=seed.content,
            metadata=dict(seed.metadata),
            score_breakdown=(
                dict(seed.score_breakdown) if seed.score_breakdown is not None else None
            ),
        )
        by_id[copied.doc_id] = copied
        direct.append(copied)
    return direct


def _opposite_endpoint(
    relation: RelationView,
    seed_id: int,
) -> tuple[int, str | None] | None:
    if relation.source_memory_id == seed_id:
        return relation.target_memory_id, relation.target_revision
    if relation.target_memory_id == seed_id:
        return relation.source_memory_id, relation.source_revision
    return None


def _privacy_allowed(item_level: str, allowed_level: str) -> bool:
    item_value = _PRIVACY_ORDER.get(item_level)
    allowed_value = _PRIVACY_ORDER.get(allowed_level)
    return (
        item_value is not None
        and allowed_value is not None
        and item_value <= allowed_value
    )


def _relation_is_current(relation: RelationView, now: datetime) -> bool:
    return visible_at(
        now,
        valid_from=relation.valid_from,
        valid_to=relation.valid_to,
        invalid_at=relation.invalid_at,
    )


def _source_is_current(
    source: MemorySourceRef | None,
    expected_revision: str,
    scope: ScopeContext,
    reference_time: datetime,
) -> bool:
    return bool(
        source is not None
        and source.revision_token == expected_revision
        and source.scope_key == scope.scope_key
        and _privacy_allowed(source.privacy_level, scope.privacy_level)
        and visible_at(
            reference_time,
            occurred_at=source.occurred_at,
            require_occurred=True,
        )
    )


def _time_decay(
    occurred_at: datetime,
    now: datetime,
    half_life_days: float,
) -> float:
    age_days = max(0.0, (now - _as_utc(occurred_at)).total_seconds() / 86_400)
    return 0.5 ** (age_days / half_life_days)


def _as_utc(value: datetime) -> datetime:
    normalized = normalize_datetime(value)
    return normalized or datetime.now(timezone.utc)


__all__ = ["DerivedRelationExpander"]
