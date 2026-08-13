"""统一时间和历史 as-of 读取回归。"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from core.features.evolution.application import (
    DerivedRelationExpander,
    ProjectionBudget,
    ProjectionReader,
    ProjectionScope,
)
from core.features.evolution.domain import (
    DerivedApplyPlan,
    DerivedState,
    ExpansionBudget,
    ProjectionBundle,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
    ScopeContext,
)
from core.features.evolution.infrastructure import MemoryEvolutionStore
from core.retrieval.query_rewriter import QueryIntent, resolve_reference_time
from core.retrieval.rrf_fusion import HybridResult
from core.shared.contracts import MemorySourceRef
from core.shared.temporal import (
    canonical_visible_at,
    infer_time_precision,
    parse_datetime,
    visible_at,
)

UTC = timezone.utc
AS_OF = datetime(2026, 3, 1, tzinfo=UTC)


def _candidate(memory_id: int) -> HybridResult:
    return HybridResult(
        doc_id=memory_id,
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=None,
        vector_score=None,
        content=f"canonical-{memory_id}",
        metadata={"scope_key": "private:user-a", "privacy_level": "shared"},
    )


def _source(
    memory_id: int,
    occurred_at: datetime,
    *,
    time_precision: str = "unknown",
    time_source: str = "unknown",
) -> MemorySourceRef:
    return MemorySourceRef(
        memory_id,
        f"rev-{memory_id}",
        "private:user-a",
        "shared",
        occurred_at,
        f"source-{memory_id}",
        time_source=time_source,
        time_precision=time_precision,
    )


def _bundle(
    *, source_time: datetime, valid_to: datetime | None = None
) -> ProjectionBundle:
    projection_id = "p:17:18"
    return ProjectionBundle(
        ProjectionView(
            projection_id,
            ProjectionType.EPISODE_SUMMARY,
            "历史摘要",
            (17, 18),
            "private:user-a",
            "shared",
            0.9,
            DerivedState.ACTIVE,
            None,
            valid_to,
        ),
        (
            ProjectionSourceView(
                projection_id, 17, "rev-17", "primary", 0, source_time
            ),
            ProjectionSourceView(
                projection_id, 18, "rev-18", "supporting", 1, source_time
            ),
        ),
    )


class _ProjectionStore:
    def __init__(self, bundle: ProjectionBundle, sources: dict[int, MemorySourceRef]):
        self.bundle = bundle
        self.sources = sources

    async def active_projection_bundles_for_seeds(self, *_args, **_kwargs):
        return [self.bundle]

    async def load_sources(self, memory_ids, **_kwargs):
        return [self.sources[item] for item in memory_ids if item in self.sources]


class _RelationReader:
    def __init__(self, relation: RelationView, source: MemorySourceRef):
        self.relation = relation
        self.source = source

    async def active_relations_for_seeds(self, *_args, **_kwargs):
        return [self.relation]

    async def load_sources(self, memory_ids):
        return [self.source] if self.source.memory_id in memory_ids else []


@pytest.mark.asyncio
async def test_projection_uses_reference_time_and_rejects_future_source() -> None:
    bundle = _bundle(source_time=datetime(2026, 6, 1, tzinfo=UTC))
    source_time = bundle.sources[0].occurred_at
    assert source_time is not None
    store = _ProjectionStore(
        bundle,
        {
            17: _source(17, source_time),
            18: _source(18, source_time),
        },
    )
    reader = ProjectionReader(store)
    stats = await reader.attach_with_stats(
        [_candidate(17)],
        scope=ProjectionScope("private:user-a", "shared", reference_time=AS_OF),
        budget=ProjectionBudget(),
    )
    assert "derived_projections" not in stats.candidates[0].metadata


@pytest.mark.asyncio
async def test_expander_uses_same_reference_time_for_future_source() -> None:
    relation = RelationView(
        "r:17:18",
        17,
        18,
        RelationType.RELATED,
        0.9,
        "private:user-a",
        "shared",
        source_revision="rev-17",
        target_revision="rev-18",
    )
    reader = _RelationReader(relation, _source(18, datetime(2026, 6, 1, tzinfo=UTC)))
    expanded = await DerivedRelationExpander(reader).expand(
        [_candidate(17)],
        scope=ScopeContext("private:user-a", "shared"),
        budget=ExpansionBudget(),
        reference_time=AS_OF,
    )
    assert [item.doc_id for item in expanded] == [17]


def test_time_parser_and_closed_interval_are_deterministic() -> None:
    assert parse_datetime("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert infer_time_precision("2026-01-01") == "day"
    assert infer_time_precision("not-a-time") == "unknown"
    assert visible_at(AS_OF, valid_from=AS_OF, valid_to=AS_OF)
    assert not visible_at(AS_OF, valid_from=datetime(2026, 3, 2, tzinfo=UTC))
    assert not visible_at(AS_OF, invalid_at=AS_OF)


def test_canonical_future_metadata_is_filtered_without_using_updated_at() -> None:
    assert not canonical_visible_at({"occurred_at": "2026-06-01T00:00:00Z"}, AS_OF)
    assert canonical_visible_at({"updated_at": "2026-06-01T00:00:00Z"}, AS_OF)


def test_query_intent_resolves_explicit_iso_reference_time() -> None:
    intent = QueryIntent(time_reference="2026-03-01T00:00:00Z")
    assert resolve_reference_time(intent) == AS_OF


def test_cache_key_separates_historical_reference_times() -> None:
    from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer

    optimizer = RetrievalOptimizer({})
    old = optimizer.cache_key("q", 5, "s", None, reference_time=AS_OF)
    new = optimizer.cache_key(
        "q", 5, "s", None, reference_time=datetime(2026, 7, 1, tzinfo=UTC)
    )
    assert old != new


@pytest.mark.asyncio
async def test_store_round_trips_temporal_provenance(tmp_path) -> None:
    store = MemoryEvolutionStore(str(tmp_path / "temporal.db"))
    await store.initialize()
    relation = RelationView(
        "r:temporal",
        17,
        18,
        RelationType.RELATED,
        0.9,
        "private:user-a",
        "shared",
        source_revision="rev-17",
        target_revision="rev-18",
        valid_from=AS_OF,
        reference_at=AS_OF,
        discovered_at=AS_OF,
        time_source="metadata",
        time_precision="day",
    )
    projection = ProjectionView(
        "p:temporal",
        ProjectionType.EPISODE_SUMMARY,
        "时间摘要",
        (17, 18),
        "private:user-a",
        "shared",
        0.8,
        valid_from=AS_OF,
        reference_at=AS_OF,
        discovered_at=AS_OF,
        time_source="metadata",
        time_precision="day",
    )
    plan = DerivedApplyPlan(
        relations=(relation,),
        projections=(projection,),
        projection_sources=(
            ProjectionSourceView("p:temporal", 17, "rev-17", "primary", 0, AS_OF),
            ProjectionSourceView("p:temporal", 18, "rev-18", "supporting", 1, AS_OF),
        ),
    )
    await store.apply_derived_plan(plan)
    stored_relation = (await store.active_relations_for_seeds((17,)))[0]
    stored_bundle = (await store.active_projection_bundles_for_seeds((17,)))[0]
    assert stored_relation.reference_at == AS_OF
    assert stored_relation.discovered_at == AS_OF
    assert stored_relation.time_source == "metadata"
    assert stored_relation.time_precision == "day"
    assert stored_bundle.projection.reference_at == AS_OF
    assert stored_bundle.projection.time_source == "metadata"
    assert stored_bundle.sources[0].occurred_at == AS_OF
    await store.close()


@pytest.mark.asyncio
async def test_store_migrates_legacy_derived_time_columns(tmp_path) -> None:
    path = str(tmp_path / "legacy-temporal.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE memory_relations (
            relation_id TEXT PRIMARY KEY, relation_key TEXT NOT NULL UNIQUE,
            source_memory_id INTEGER NOT NULL, target_memory_id INTEGER NOT NULL,
            source_revision TEXT NOT NULL, target_revision TEXT NOT NULL,
            relation_type TEXT NOT NULL, state TEXT NOT NULL, confidence REAL NOT NULL,
            scope_key TEXT NOT NULL, privacy_level TEXT NOT NULL,
            valid_from TEXT, valid_to TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        await db.commit()
    store = MemoryEvolutionStore(path)
    await store.initialize()
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("PRAGMA table_info(memory_relations)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
    assert {
        "reference_at",
        "discovered_at",
        "invalid_at",
        "time_source",
        "time_precision",
    } <= columns
    await store.close()


@pytest.mark.asyncio
async def test_deleting_supporting_source_preserves_primary_projection(
    tmp_path,
) -> None:
    store = MemoryEvolutionStore(str(tmp_path / "multi-source.db"))
    await store.initialize()
    projection = ProjectionView(
        "p:shared",
        ProjectionType.EPISODE_SUMMARY,
        "共享来源摘要",
        (17, 18),
        "private:user-a",
        "shared",
        0.8,
    )
    await store.apply_derived_plan(
        DerivedApplyPlan(
            projections=(projection,),
            projection_sources=(
                ProjectionSourceView("p:shared", 17, "rev-17", "primary", 0),
                ProjectionSourceView("p:shared", 18, "rev-18", "supporting", 1),
            ),
        )
    )
    assert await store.invalidate_for_deleted_source(18) == 0
    bundles = await store.active_projection_bundles_for_seeds((17,))
    assert len(bundles) == 1
    assert [(source.memory_id, source.role) for source in bundles[0].sources] == [
        (17, "primary")
    ]
    await store.close()


@pytest.mark.asyncio
async def test_revising_supporting_source_removes_only_that_mapping(tmp_path) -> None:
    store = MemoryEvolutionStore(str(tmp_path / "revision-multi-source.db"))
    await store.initialize()
    await store.apply_derived_plan(
        DerivedApplyPlan(
            projections=(
                ProjectionView(
                    "p:revision",
                    ProjectionType.EPISODE_SUMMARY,
                    "可部分保留的摘要",
                    (17, 18),
                    "private:user-a",
                    "shared",
                    0.8,
                ),
            ),
            projection_sources=(
                ProjectionSourceView("p:revision", 17, "rev-17", "primary", 0),
                ProjectionSourceView("p:revision", 18, "rev-18", "supporting", 1),
            ),
        )
    )
    assert await store.invalidate_for_source_revision(18, "rev-18-new") == 0
    bundles = await store.active_projection_bundles_for_seeds((17,))
    assert len(bundles) == 1
    assert [source.memory_id for source in bundles[0].sources] == [17]
    await store.close()


def test_conflict_projection_model_keeps_source_roles_and_time() -> None:
    projection = ProjectionView(
        "conflict",
        ProjectionType.CONFLICT_SET,
        "冲突",
        (1, 2, 3),
        "private:user-a",
        "shared",
        0.8,
        reference_at=AS_OF,
    )
    assert projection.reference_at == AS_OF
    assert projection.state is DerivedState.ACTIVE


@pytest.mark.asyncio
async def test_conflict_reader_marks_equal_times_unresolved_without_leaking_fields() -> (
    None
):
    projection_id = "conflict:17:18:19"
    projection = ProjectionView(
        projection_id,
        ProjectionType.CONFLICT_SET,
        "两条证据仍然冲突",
        (17, 18, 19),
        "private:user-a",
        "shared",
        0.8,
    )
    mappings = (
        ProjectionSourceView(projection_id, 17, "rev-17", "primary", 0, AS_OF),
        ProjectionSourceView(projection_id, 18, "rev-18", "conflict_left", 1, AS_OF),
        ProjectionSourceView(projection_id, 19, "rev-19", "conflict_right", 2, AS_OF),
    )
    store = _ProjectionStore(
        ProjectionBundle(projection, mappings),
        {item.memory_id: _source(item.memory_id, AS_OF) for item in mappings},
    )
    reader = ProjectionReader(store)
    stats = await reader.attach_with_stats(
        [_candidate(17)],
        scope=ProjectionScope("private:user-a", "shared", reference_time=AS_OF),
        budget=ProjectionBudget(),
    )
    assert (stats.resolved_conflicts, stats.unresolved_conflicts) == (0, 1)
    assert stats.conflict_decisions == ("unresolved",)
    assert stats.candidates[0].metadata["derived_projections"][0] == {
        "type": "conflict_set",
        "summary": "两条证据仍然冲突",
        "confidence": 0.8,
    }


@pytest.mark.asyncio
async def test_conflict_reader_orders_exact_source_times_internally() -> None:
    projection_id = "conflict:exact"
    left_time = datetime(2026, 1, 1, tzinfo=UTC)
    right_time = datetime(2026, 2, 1, tzinfo=UTC)
    projection = ProjectionView(
        projection_id,
        ProjectionType.CONFLICT_SET,
        "冲突历史",
        (17, 18, 19),
        "private:user-a",
        "shared",
        0.8,
    )
    mappings = (
        ProjectionSourceView(projection_id, 17, "rev-17", "primary", 0, left_time),
        ProjectionSourceView(
            projection_id, 18, "rev-18", "conflict_left", 1, left_time
        ),
        ProjectionSourceView(
            projection_id, 19, "rev-19", "conflict_right", 2, right_time
        ),
    )
    store = _ProjectionStore(
        ProjectionBundle(projection, mappings),
        {
            17: _source(
                17, left_time, time_precision="instant", time_source="explicit"
            ),
            18: _source(
                18, left_time, time_precision="instant", time_source="explicit"
            ),
            19: _source(
                19, right_time, time_precision="instant", time_source="explicit"
            ),
        },
    )
    reader = ProjectionReader(store)
    stats = await reader.attach_with_stats(
        [_candidate(17)],
        scope=ProjectionScope("private:user-a", "shared", reference_time=AS_OF),
        budget=ProjectionBudget(),
    )
    assert (stats.resolved_conflicts, stats.unresolved_conflicts) == (1, 0)
    assert stats.conflict_decisions == ("conflict_right_newer",)
