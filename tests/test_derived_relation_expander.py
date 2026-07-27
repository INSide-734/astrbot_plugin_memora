from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.models.memory_evolution import (
    ExpansionBudget,
    MemorySourceRef,
    RelationType,
    RelationView,
    ScopeContext,
)
from core.retrieval.rrf_fusion import HybridResult

UTC = timezone.utc
NOW = datetime.now(UTC)


def result(doc_id: int, score: float, content: str | None = None) -> HybridResult:
    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=content or f"记忆 {doc_id}",
        metadata={"scope_key": "private:user-a", "privacy_level": "shared"},
    )


def relation(
    source_id: int,
    target_id: int,
    *,
    confidence: float = 0.8,
    scope_key: str = "private:user-a",
    privacy_level: str = "shared",
    source_revision: str | None = None,
    target_revision: str | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> RelationView:
    return RelationView(
        relation_id=f"r:{source_id}:{target_id}",
        source_memory_id=source_id,
        target_memory_id=target_id,
        relation_type=RelationType.SAME_EPISODE,
        confidence=confidence,
        scope_key=scope_key,
        privacy_level=privacy_level,
        source_revision=source_revision or f"rev-{source_id}",
        target_revision=target_revision or f"rev-{target_id}",
        valid_from=valid_from,
        valid_to=valid_to,
    )


def source(
    memory_id: int,
    *,
    scope_key: str = "private:user-a",
    privacy_level: str = "shared",
    revision_token: str | None = None,
    content: str | None = None,
) -> MemorySourceRef:
    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision_token or f"rev-{memory_id}",
        scope_key=scope_key,
        privacy_level=privacy_level,
        occurred_at=NOW,
        content=content or f"记忆 {memory_id}",
    )


class FakeDerivedReader:
    def __init__(
        self,
        relations: dict[int, list[RelationView]],
        sources: dict[int, MemorySourceRef],
    ) -> None:
        self.relations = relations
        self.sources = sources
        self.requested_seed_batches: list[tuple[int, ...]] = []

    async def active_relations_for_seeds(self, seed_ids, scope_key, limit):
        seed_batch = tuple(int(item) for item in seed_ids)
        self.requested_seed_batches.append(seed_batch)
        return [
            item
            for seed_id in seed_batch
            for item in self.relations.get(seed_id, [])
            if item.scope_key == scope_key
        ][:limit]

    async def load_sources(self, memory_ids):
        return [
            self.sources[memory_id]
            for memory_id in memory_ids
            if memory_id in self.sources
        ]


def private_scope(privacy_level: str = "shared") -> ScopeContext:
    return ScopeContext(
        scope_key="private:user-a",
        privacy_level=privacy_level,
    )


@pytest.mark.asyncio
async def test_expander_is_one_hop_and_preserves_direct_seed() -> None:
    from core.retrieval.derived_relation_expander import DerivedRelationExpander

    reader = FakeDerivedReader(
        relations={
            17: [relation(17, 18)],
            18: [relation(18, 19)],
        },
        sources={18: source(18), 19: source(19)},
    )
    expander = DerivedRelationExpander(reader, per_seed_limit=2, global_limit=4)

    expanded = await expander.expand(
        [result(17, 0.9)],
        scope=private_scope(),
        budget=ExpansionBudget(max_chars=500, max_items=8),
    )

    assert [item.doc_id for item in expanded] == [17, 18]
    assert expanded[0].final_score == 0.9
    assert 19 not in [item.doc_id for item in expanded]
    assert reader.requested_seed_batches == [(17,)]


@pytest.mark.asyncio
async def test_expander_enforces_per_seed_global_item_and_character_budgets() -> None:
    from core.retrieval.derived_relation_expander import DerivedRelationExpander

    reader = FakeDerivedReader(
        relations={
            17: [relation(17, 18), relation(17, 19)],
            20: [relation(20, 21)],
        },
        sources={
            18: source(18, content="十八"),
            19: source(19, content="十九"),
            21: source(21, content="二十一"),
        },
    )
    expander = DerivedRelationExpander(reader, per_seed_limit=1, global_limit=1)

    expanded = await expander.expand(
        [result(17, 0.9, "十七"), result(20, 0.8, "二十")],
        scope=private_scope(),
        budget=ExpansionBudget(max_chars=6, max_items=3),
    )

    assert [item.doc_id for item in expanded] == [17, 20, 18]


@pytest.mark.asyncio
async def test_expander_filters_scope_privacy_revision_and_expired_relations() -> None:
    from core.retrieval.derived_relation_expander import DerivedRelationExpander

    reader = FakeDerivedReader(
        relations={
            17: [
                relation(17, 18, scope_key="private:other"),
                relation(17, 19, privacy_level="confidential"),
                relation(17, 20, target_revision="stale"),
                relation(17, 21, valid_to=NOW - timedelta(seconds=1)),
            ]
        },
        sources={
            18: source(18, scope_key="private:other"),
            19: source(19, privacy_level="confidential"),
            20: source(20),
            21: source(21),
        },
    )
    expander = DerivedRelationExpander(reader, per_seed_limit=8, global_limit=8)

    expanded = await expander.expand(
        [result(17, 0.9)],
        scope=private_scope("shared"),
        budget=ExpansionBudget(max_chars=500, max_items=8),
    )

    assert [item.doc_id for item in expanded] == [17]


@pytest.mark.asyncio
async def test_expander_deduplicates_and_uses_max_direct_or_derived_score() -> None:
    from core.retrieval.derived_relation_expander import DerivedRelationExpander

    reader = FakeDerivedReader(
        relations={17: [relation(17, 18, confidence=1.0)]},
        sources={18: source(18)},
    )
    expander = DerivedRelationExpander(reader, per_seed_limit=4, global_limit=4)

    expanded = await expander.expand(
        [result(17, 0.9), result(18, 0.95), result(18, 0.4)],
        scope=private_scope(),
        budget=ExpansionBudget(max_chars=500, max_items=8),
    )

    assert [item.doc_id for item in expanded] == [17, 18]
    assert expanded[1].final_score == 0.95


@pytest.mark.asyncio
async def test_expander_returns_direct_results_when_reader_fails() -> None:
    from core.retrieval.derived_relation_expander import DerivedRelationExpander

    reader = FakeDerivedReader({}, {})

    async def fail_reader(*_args, **_kwargs):
        raise RuntimeError("读取失败")

    reader.active_relations_for_seeds = fail_reader
    expander = DerivedRelationExpander(reader)
    seeds = [result(17, 0.9)]

    expanded = await expander.expand(
        seeds,
        scope=private_scope(),
        budget=ExpansionBudget(max_chars=500, max_items=8),
    )

    assert expanded == seeds
