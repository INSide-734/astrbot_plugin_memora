import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import aiosqlite
import pytest

from core.features.evolution.domain import (
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
from core.features.evolution.infrastructure import MemoryEvolutionStore

UTC = timezone.utc


def job_spec(key: str, *, not_before: datetime | None = None) -> JobSpec:
    """构造可控的演化 job；测试需要时允许显式指定可领取时间。"""

    scheduled_at = not_before or datetime.now(UTC)
    return JobSpec("scope", "bucket", (17,), key, scheduled_at)


def valid_plan() -> DerivedApplyPlan:
    return DerivedApplyPlan(
        relations=(
            RelationView(
                "r1",
                17,
                18,
                RelationType.SAME_EPISODE,
                0.9,
                "scope",
                "shared",
                DerivedState.ACTIVE,
                "r17",
                "r18",
            ),
        ),
        projections=(
            ProjectionView(
                "p1",
                ProjectionType.EPISODE_SUMMARY,
                "episode",
                (17, 18),
                "scope",
                "shared",
                0.8,
            ),
        ),
        projection_sources=(
            ProjectionSourceView("p1", 17, "r17", "primary", 0),
            ProjectionSourceView("p1", 18, "r18", "supporting", 1),
        ),
    )


@pytest.mark.asyncio
async def test_initialize_creates_tables(tmp_path):
    path = str(tmp_path / "memory.db")
    store = MemoryEvolutionStore(path)
    await store.initialize()
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
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
    assert [
        (source.memory_id, source.role, source.ordinal) for source in bundle.sources
    ] == [
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
                    "p0",
                    ProjectionType.PREFERENCE_STATE,
                    "preference",
                    (18, 19),
                    "scope",
                    "shared",
                    0.8,
                ),
                ProjectionView(
                    "p3",
                    ProjectionType.RELATIONSHIP_STATE,
                    "relationship",
                    (18, 20),
                    "scope",
                    "shared",
                    0.8,
                ),
                ProjectionView(
                    "inactive",
                    ProjectionType.EPISODE_SUMMARY,
                    "inactive",
                    (18, 21),
                    "scope",
                    "shared",
                    0.99,
                    DerivedState.INVALIDATED,
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
    assert (
        await store.active_projection_bundles_for_seeds([18], scope_key="other") == []
    )
    assert await store.active_projection_bundles_for_seeds([18], limit=0) == []
    await store.close()


@pytest.mark.asyncio
async def test_load_sources_excludes_inactive_documents_by_default(tmp_path):
    """演化证据默认只读取 active canonical 记忆，失效路径可显式读取原 revision。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT, created_at TEXT, updated_at TEXT)"
        )
        await db.executemany(
            "INSERT INTO documents (id, text, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    17,
                    "活跃证据",
                    '{"scope_key":"scope","privacy_level":"shared"}',
                    "2026-07-21T00:00:00+00:00",
                    "r17",
                ),
                (
                    18,
                    "休眠证据",
                    '{"scope_key":"scope","privacy_level":"shared","memory_status":"dormant","status":"dormant"}',
                    "2026-07-21T00:00:00+00:00",
                    "r18",
                ),
            ],
        )
        await db.commit()

    assert [source.memory_id for source in await store.load_sources((17, 18))] == [17]
    assert [
        source.memory_id
        for source in await store.load_sources((17, 18), active_only=False)
    ] == [17, 18]
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
    assert (
        await store.recover_expired_leases(datetime.now(UTC) + timedelta(seconds=2))
        == 1
    )
    stored = await store.get_job(first.job_id)
    assert stored is not None
    assert stored.state is JobState.PENDING
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
                "r2",
                17,
                18,
                RelationType.SAME_EPISODE,
                0.8,
                "scope",
                "shared",
                DerivedState.ACTIVE,
                "r17-new",
                "r18",
            ),
        ),
    )
    await store.apply_derived_plan(first)
    await store.apply_derived_plan(second)
    async with aiosqlite.connect(store.db_path) as db:
        row = await (
            await db.execute("SELECT COUNT(*) FROM memory_relations")
        ).fetchone()
        assert row is not None
        count = row[0]
    assert count == 2
    await store.close()


@pytest.mark.asyncio
async def test_projection_primary_role_and_conflict_roles_are_persisted(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    plan = DerivedApplyPlan(
        projections=(
            ProjectionView(
                "conflict-1",
                ProjectionType.CONFLICT_SET,
                "conflicting evidence",
                (17, 18, 19),
                "scope",
                "shared",
                0.8,
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
        projection = await (
            await db.execute("SELECT primary_source_memory_id FROM memory_projections")
        ).fetchone()
        roles = {
            row["source_role"]
            for row in await (
                await db.execute("SELECT source_role FROM memory_projection_sources")
            ).fetchall()
        }
    assert projection is not None
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
        row = await (
            await db.execute("SELECT COUNT(*) FROM memory_relations")
        ).fetchone()
        assert row is not None
        relation_count = row[0]
    assert relation_count == 0
    await store.close()


@pytest.mark.asyncio
async def test_invalidate_source_revision_removes_active_relation_and_projection(
    tmp_path,
):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(valid_plan())
    assert await store.invalidate_for_source_revision(17, "new-r17") == 2
    assert await store.active_relations_for_seeds([17]) == []
    assert await store.active_projections_for_seeds([17]) == []
    await store.close()


@pytest.mark.asyncio
async def test_derived_writes_are_serialized_on_shared_connection(tmp_path):
    """派生写事务不能在同一持久连接上交叠。"""

    class GateLock:
        """让测试确定性观察第二个写操作等待第一个事务释放。"""

        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self.first_entered = asyncio.Event()
            self.second_waiting = asyncio.Event()
            self.release_first = asyncio.Event()
            self._first = True

        async def __aenter__(self):
            """获取测试锁，并阻塞首个持锁者以制造可控竞争。"""

            if self._lock.locked():
                self.second_waiting.set()
            await self._lock.acquire()
            if self._first:
                self._first = False
                self.first_entered.set()
                await self.release_first.wait()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            """释放测试锁并保留被包装协程的异常语义。"""

            self._lock.release()
            return False

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    gate = GateLock()
    cast(Any, store)._write_lock = gate
    apply_task = asyncio.create_task(store.apply_derived_plan(valid_plan()))
    await gate.first_entered.wait()
    invalidate_task = asyncio.create_task(
        store.invalidate_for_source_revision(17, "new-r17")
    )
    await gate.second_waiting.wait()
    assert not invalidate_task.done()
    gate.release_first.set()
    await asyncio.gather(apply_task, invalidate_task)
    assert await store.active_relations_for_seeds([17]) == []
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
    first = await store.enqueue_job(job_spec("first", not_before=now))
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
    stored_first = await store.get_job(first.job_id)
    assert stored_first is not None
    assert stored_first.state is JobState.COMPLETED

    second = await store.enqueue_job(job_spec("second"))
    second_claim = await store.claim_job(
        datetime.now(UTC) + timedelta(seconds=1),
        30,
        worker_token="worker-3",
    )
    assert second_claim is not None
    assert await store.reject_job(second.job_id, "worker-3", "invalid_alias")
    stored_second = await store.get_job(second.job_id)
    assert stored_second is not None
    assert stored_second.state is JobState.REJECTED
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
