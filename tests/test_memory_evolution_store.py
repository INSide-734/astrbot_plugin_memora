import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from core.models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    JobSpec,
    JobState,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
    RetrySpec,
)
from core.storage.memory_evolution_store import MemoryEvolutionStore


UTC = timezone.utc


def job_spec(key: str) -> JobSpec:
    now = datetime.now(UTC)
    return JobSpec("scope", "bucket", (17,), key, now)


def valid_plan() -> DerivedApplyPlan:
    return DerivedApplyPlan(
        relations=(RelationView("r1", 17, 18, RelationType.SAME_EPISODE, .9, "scope", "shared", DerivedState.ACTIVE, "r17", "r18"),),
        projections=(ProjectionView("p1", ProjectionType.EPISODE_SUMMARY, "episode", (17, 18), "scope", "shared", .8),),
        projection_sources=(ProjectionSourceView("p1", 17, "r17", "primary", 0), ProjectionSourceView("p1", 18, "r18", "supporting", 1)),
    )


@pytest.mark.asyncio
async def test_initialize_creates_tables(tmp_path):
    path = str(tmp_path / "memory.db")
    store = MemoryEvolutionStore(path)
    await store.initialize()
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
        names = {row[0] for row in await cur.fetchall()}
    assert {
        "memory_evolution_jobs",
        "memory_relations",
        "memory_projections",
        "memory_projection_sources",
        "idx_memory_evolution_jobs_ready",
        "idx_memory_relations_seed",
        "idx_memory_projection_sources_memory",
    } <= names
    await store.close()


@pytest.mark.asyncio
async def test_apply_plan_and_query(tmp_path):
    path = str(tmp_path / "memory.db")
    store = MemoryEvolutionStore(path)
    await store.initialize()
    await store.apply_derived_plan(valid_plan())
    assert len(await store.active_relations_for_seeds([17])) == 1
    assert len(await store.active_projections_for_seeds([17])) == 1
    await store.close()


@pytest.mark.asyncio
async def test_projection_bundle_returns_source_mapping_for_supporting_seed(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(valid_plan())

    bundles = await store.active_projection_bundles_for_seeds([18])

    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle.projection.projection_id == "p1"
    assert bundle.projection.source_memory_ids == (17, 18)
    assert [(source.memory_id, source.role, source.ordinal) for source in bundle.sources] == [
        (17, "primary", 0),
        (18, "supporting", 1),
    ]
    await store.close()


@pytest.mark.asyncio
async def test_projection_bundle_filters_scope_state_and_orders_ties(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(valid_plan())
    await store.apply_derived_plan(
        DerivedApplyPlan(
            projections=(
                ProjectionView(
                    "p0", ProjectionType.PREFERENCE_STATE, "preference", (18, 19),
                    "scope", "shared", .8,
                ),
                ProjectionView(
                    "p3", ProjectionType.RELATIONSHIP_STATE, "relationship", (18, 20),
                    "scope", "shared", .8,
                ),
                ProjectionView(
                    "inactive", ProjectionType.EPISODE_SUMMARY, "inactive", (18, 21),
                    "scope", "shared", .99, DerivedState.INVALIDATED,
                ),
            ),
            projection_sources=(
                ProjectionSourceView("p0", 18, "r18", "primary", 0),
                ProjectionSourceView("p0", 19, "r19", "supporting", 1),
                ProjectionSourceView("p3", 18, "r18", "primary", 0),
                ProjectionSourceView("p3", 20, "r20", "supporting", 1),
                ProjectionSourceView("inactive", 18, "r18", "primary", 0),
                ProjectionSourceView("inactive", 21, "r21", "supporting", 1),
            ),
        )
    )

    bundles = await store.active_projection_bundles_for_seeds([18], scope_key="scope")

    assert [bundle.projection.projection_id for bundle in bundles] == ["p0", "p1", "p3"]
    assert await store.active_projection_bundles_for_seeds([18], scope_key="other") == []
    assert await store.active_projection_bundles_for_seeds([18], limit=0) == []
    await store.close()


@pytest.mark.asyncio
async def test_idempotent_enqueue_and_expired_lease(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    first = await store.enqueue_job(job_spec("key"))
    second = await store.enqueue_job(job_spec("key"))
    assert first.job_id == second.job_id
    claim = await store.claim_job(datetime.now(UTC), 1)
    assert claim is not None
    assert await store.recover_expired_leases(datetime.now(UTC) + timedelta(seconds=2)) == 1
    assert (await store.get_job(first.job_id)).state is JobState.PENDING
    await store.close()


@pytest.mark.asyncio
async def test_initialize_migrates_job_source_revision_column(tmp_path):
    """旧数据库缺少 source revision 列时必须原地补齐且不删除 job。"""

    path = str(tmp_path / "memory.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """CREATE TABLE memory_evolution_jobs (
            job_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, bucket_key TEXT NOT NULL,
            state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
            source_ids_json TEXT NOT NULL DEFAULT '[]', not_before TEXT NOT NULL,
            lease_until TEXT, worker_token TEXT, idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error_code TEXT
            )"""
        )
        await db.commit()

    store = MemoryEvolutionStore(path)
    await store.initialize()
    async with aiosqlite.connect(path) as db:
        columns = await db.execute("PRAGMA table_info(memory_evolution_jobs)")
        names = {row[1] for row in await columns.fetchall()}

    assert "source_revisions_json" in names
    await store.close()


@pytest.mark.asyncio
async def test_job_persists_source_revisions_into_claim(tmp_path):
    """job 必须保留入队时 revision，claim 不能只携带 canonical ID。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    spec = JobSpec(
        "scope",
        "bucket",
        (17,),
        "revision-key",
        datetime.now(UTC),
        source_revisions={17: "r17"},
    )

    job = await store.enqueue_job(spec)
    claim = await store.claim_job(datetime.now(UTC), 30)

    assert job.source_revisions == {17: "r17"}
    assert claim is not None
    assert claim.source_revisions == {17: "r17"}
    await store.close()


@pytest.mark.asyncio
async def test_relation_identity_includes_source_revisions(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    first = valid_plan()
    second = DerivedApplyPlan(
        relations=(
            RelationView(
                "r2", 17, 18, RelationType.SAME_EPISODE, .8,
                "scope", "shared", DerivedState.ACTIVE, "r17-new", "r18",
            ),
        ),
    )
    await store.apply_derived_plan(first)
    await store.apply_derived_plan(second)
    async with aiosqlite.connect(store.db_path) as db:
        count = (await (await db.execute("SELECT COUNT(*) FROM memory_relations")).fetchone())[0]
    assert count == 2
    await store.close()


@pytest.mark.asyncio
async def test_projection_primary_role_and_conflict_roles_are_persisted(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    plan = DerivedApplyPlan(
        projections=(
            ProjectionView(
                "conflict-1", ProjectionType.CONFLICT_SET, "conflicting evidence",
                (17, 18, 19), "scope", "shared", .8,
            ),
        ),
        projection_sources=(
            ProjectionSourceView("conflict-1", 18, "r18", "conflict_left", 1),
            ProjectionSourceView("conflict-1", 19, "r19", "conflict_right", 2),
            ProjectionSourceView("conflict-1", 17, "r17", "primary", 0),
        ),
    )
    await store.apply_derived_plan(plan)
    async with aiosqlite.connect(store.db_path) as db:
        db.row_factory = aiosqlite.Row
        projection = await (await db.execute(
            "SELECT primary_source_memory_id FROM memory_projections"
        )).fetchone()
        roles = {
            row["source_role"]
            for row in await (await db.execute(
                "SELECT source_role FROM memory_projection_sources"
            )).fetchall()
        }
    assert projection["primary_source_memory_id"] == 17
    assert {"primary", "conflict_left", "conflict_right"} == roles
    await store.close()


@pytest.mark.asyncio
async def test_apply_plan_rolls_back_relation_when_projection_is_invalid(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    invalid = DerivedApplyPlan(
        relations=valid_plan().relations,
        projections=valid_plan().projections,
        projection_sources=(
            ProjectionSourceView("p1", 17, "r17", "supporting", 0),
            ProjectionSourceView("p1", 18, "r18", "supporting", 1),
        ),
    )
    with pytest.raises(ValueError, match="exactly one primary"):
        await store.apply_derived_plan(invalid)
    async with aiosqlite.connect(store.db_path) as db:
        relation_count = (await (await db.execute(
            "SELECT COUNT(*) FROM memory_relations"
        )).fetchone())[0]
    assert relation_count == 0
    await store.close()


@pytest.mark.asyncio
async def test_invalidate_source_revision_removes_active_relation_and_projection(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(valid_plan())
    assert await store.invalidate_for_source_revision(17, "new-r17") == 2
    assert await store.active_relations_for_seeds([17]) == []
    assert await store.active_projections_for_seeds([17]) == []
    await store.close()


@pytest.mark.asyncio
async def test_relation_query_finds_seed_on_either_endpoint(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(valid_plan())
    assert len(await store.active_relations_for_seeds([17])) == 1
    assert len(await store.active_relations_for_seeds([18])) == 1
    await store.close()


@pytest.mark.asyncio
async def test_job_retry_renew_complete_and_reject_transitions(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    now = datetime.now(UTC)
    first = await store.enqueue_job(job_spec("first"))
    first_claim = await store.claim_job(now, 30, worker_token="worker-1")
    assert first_claim is not None
    renewed_until = now + timedelta(seconds=60)
    assert await store.renew_lease(first.job_id, "worker-1", renewed_until)
    assert await store.retry_job(
        first.job_id,
        "worker-1",
        RetrySpec(now - timedelta(seconds=1), 1, "provider_unavailable"),
    )
    retry_claim = await store.claim_job(now, 30, worker_token="worker-2")
    assert retry_claim is not None
    assert await store.complete_job(first.job_id, "worker-2")
    assert (await store.get_job(first.job_id)).state is JobState.COMPLETED

    second = await store.enqueue_job(job_spec("second"))
    second_claim = await store.claim_job(
        datetime.now(UTC) + timedelta(seconds=1),
        30,
        worker_token="worker-3",
    )
    assert second_claim is not None
    assert await store.reject_job(second.job_id, "worker-3", "invalid_alias")
    assert (await store.get_job(second.job_id)).state is JobState.REJECTED
    await store.close()


@pytest.mark.asyncio
async def test_concurrent_claim_has_single_valid_owner(tmp_path):
    """两个 Store 同时 claim 时只能有一个 worker 获得 lease。"""

    path = str(tmp_path / "memory.db")
    first_store = MemoryEvolutionStore(path)
    second_store = MemoryEvolutionStore(path)
    await first_store.initialize()
    await second_store.initialize()
    try:
        await first_store.enqueue_job(job_spec("concurrent-claim"))
        now = datetime.now(UTC)

        claims = await asyncio.gather(
            first_store.claim_job(now, 30, worker_token="worker-a"),
            second_store.claim_job(now, 30, worker_token="worker-b"),
        )

        valid_claims = [claim for claim in claims if claim is not None]
        assert len(valid_claims) == 1
        assert valid_claims[0].worker_token in {"worker-a", "worker-b"}
    finally:
        await second_store.close()
        await first_store.close()
