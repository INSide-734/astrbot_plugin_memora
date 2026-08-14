"""Episode/Conflict 候选接入 Memory Evolution 的生产闭环测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from core.features.evolution.application import (
    MemoryEvolutionCandidateGenerator,
    MemoryEvolutionGate,
    MemoryEvolutionManager,
)
from core.features.evolution.domain import (
    DerivedState,
    EvolutionProposal,
    JobState,
    MemoryRelationProposal,
    RelationType,
)
from core.features.evolution.infrastructure import MemoryEvolutionStore

UTC = timezone.utc
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)


def evolution_config() -> dict:
    """返回启用派生 worker 的最小测试配置。"""

    return {
        "enabled": True,
        "mode": "active",
        "trigger_threshold": 0.5,
        "max_pending_jobs": 20,
        "max_attempts": 2,
        "lease_seconds": 30,
        "retry_base_delay_seconds": 1,
        "candidate_limit": 16,
        "auto_active_relation_types": ["same_episode", "supports", "related"],
    }


@pytest_asyncio.fixture
async def pipeline(tmp_path):
    """装配真实 Store、Manager 和确定性候选生成器。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    consolidator = AsyncMock()
    consolidator.propose.return_value = EvolutionProposal()
    generator = MemoryEvolutionCandidateGenerator(
        episode_config={
            "enabled": True,
            "time_window_hours": 24,
            "topic_overlap_threshold": 0.3,
        },
        contradiction_config={"jaccard_threshold": 0.3},
    )
    manager = MemoryEvolutionManager(
        store,
        MemoryEvolutionGate(evolution_config()),
        consolidator,
        evolution_config(),
        candidate_generator=generator,
    )
    yield store, manager, consolidator
    await manager.stop()
    await store.close()


async def seed_document(
    store: MemoryEvolutionStore,
    memory_id: int,
    content: str,
    *,
    hours_ago: int,
    topics: tuple[str, ...] = ("咖啡",),
    participant_id: str = "user-a",
    revision: str | None = None,
    scope_key: str = "private:scope-a",
) -> None:
    """写入带 scope、privacy、topic 和稳定参与者证据的 canonical 行。"""

    metadata = {
        "scope_key": scope_key,
        "privacy_level": "confidential",
        "topics": list(topics),
        "participant_ids": [participant_id],
        "occurred_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents "
            "(id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        await db.execute(
            "INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                f"d{memory_id}",
                content,
                json.dumps(metadata, ensure_ascii=False),
                (NOW - timedelta(hours=hours_ago)).isoformat(),
                revision or f"r-{memory_id}",
            ),
        )
        await db.commit()


async def relation_rows(store: MemoryEvolutionStore) -> list[aiosqlite.Row]:
    """读取测试断言所需的派生关系行。"""

    async with aiosqlite.connect(store.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT relation_id,source_memory_id,target_memory_id,source_revision,"
            "target_revision,relation_type,state,valid_from,valid_to,origin_job_id "
            "FROM memory_relations "
            "ORDER BY relation_type,source_memory_id,target_memory_id"
        )
        return list(await cursor.fetchall())


@pytest.mark.asyncio
async def test_episode_candidate_is_scheduled_and_written_without_llm(pipeline) -> None:
    """新 canonical 写入应自动携带近邻 source 并落为 same_episode relation。"""

    store, manager, consolidator = pipeline
    await seed_document(store, 1, "在咖啡店点了拿铁", hours_ago=2)
    await seed_document(store, 2, "随后又聊到咖啡豆", hours_ago=1)
    source = (await store.load_sources((2,)))[0]

    decision = await manager.schedule_consider(source)
    claim = await store.claim_job(
        datetime.now(UTC) + timedelta(seconds=1),
        30,
        worker_token="inspect",
    )

    assert decision.should_enqueue is True
    assert claim is not None
    assert claim.source_ids == (2, 1)
    assert claim.source_revisions == {2: "r-2", 1: "r-1"}
    await store.restore_pending(claim.job_id, claim.worker_token)
    assert await manager.run_once() is True
    rows = await relation_rows(store)
    assert len(rows) == 1
    assert rows[0]["relation_type"] == "same_episode"
    assert rows[0]["state"] == DerivedState.ACTIVE.value
    assert rows[0]["source_revision"] in {"r-1", "r-2"}
    assert rows[0]["target_revision"] in {"r-1", "r-2"}
    assert rows[0]["valid_from"] == (NOW - timedelta(hours=2)).isoformat()
    assert rows[0]["valid_to"] == (NOW - timedelta(hours=1)).isoformat()
    consolidator.propose.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflict_candidate_stays_review_gated_and_auditable(pipeline) -> None:
    """高影响冲突只写 candidate，且 relation 可回指已完成 job 和 revisions。"""

    store, manager, consolidator = pipeline
    await seed_document(store, 1, "我一直喜欢喝咖啡", hours_ago=1)
    await seed_document(store, 2, "我现在不再喜欢喝咖啡", hours_ago=0)
    source = (await store.load_sources((2,)))[0]

    await manager.schedule_consider(source)
    assert await manager.run_once() is True

    rows = await relation_rows(store)
    conflict = next(row for row in rows if row["relation_type"] == "contradicts")
    assert conflict["state"] == DerivedState.CANDIDATE.value
    assert conflict["source_revision"] == "r-2"
    assert conflict["target_revision"] == "r-1"
    assert conflict["valid_from"] == (NOW - timedelta(hours=1)).isoformat()
    assert conflict["valid_to"] == NOW.isoformat()
    assert conflict["origin_job_id"]
    job = await store.get_job(conflict["origin_job_id"])
    assert job is not None
    assert job.state is JobState.COMPLETED
    consolidator.propose.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_subject_private_conflict_is_rejected_by_manager(pipeline) -> None:
    """即使 Provider 提出冲突，不同私聊主体也必须在 Manager 边界被拒绝。"""

    store, manager, consolidator = pipeline
    await seed_document(
        store,
        1,
        "我一直喜欢喝咖啡",
        hours_ago=1,
        participant_id="user-a",
    )
    await seed_document(
        store,
        2,
        "我现在不再喜欢喝咖啡",
        hours_ago=0,
        participant_id="user-b",
    )
    consolidator.propose.return_value = EvolutionProposal(
        relations=(
            MemoryRelationProposal(
                "M1",
                "M2",
                RelationType.CONTRADICTS,
                0.9,
                "候选",
                None,
                None,
            ),
        )
    )
    source = (await store.load_sources((2,)))[0]

    await manager.schedule_consider(source)
    assert await manager.run_once() is True

    assert await relation_rows(store) == []
    assert manager.get_status_snapshot()["reason_codes"]["subject_mismatch"] == 1


@pytest.mark.asyncio
async def test_candidate_source_selection_filters_scope_before_limit(pipeline) -> None:
    """其他 scope 的近期行不得挤掉同 scope 的较早候选。"""

    store, _manager, _consolidator = pipeline
    await seed_document(store, 1, "同一会话的早期事实", hours_ago=3)
    for memory_id in range(2, 26):
        await seed_document(
            store,
            memory_id,
            f"其他会话事实 {memory_id}",
            hours_ago=2,
            scope_key="private:other",
        )
    await seed_document(store, 26, "同一会话的新事实", hours_ago=1)
    primary = (await store.load_sources((26,)))[0]

    selected = await store.load_candidate_sources(primary, limit=6)

    assert [source.memory_id for source in selected] == [26, 1]


@pytest.mark.asyncio
async def test_rebuild_replays_episode_idempotently_from_canonical(pipeline) -> None:
    """派生重建应从 canonical 重放 episode，重复 job 只保留一条稳定 relation。"""

    store, manager, _consolidator = pipeline
    await seed_document(store, 1, "在咖啡店点了拿铁", hours_ago=2)
    await seed_document(store, 2, "随后又聊到咖啡豆", hours_ago=1)

    source = (await store.load_sources((2,)))[0]
    await manager.schedule_consider(source)
    assert await manager.run_once() is True

    result = await manager.rebuild_from_canonical()
    replay_claim = await store.claim_job(
        datetime.now(UTC) + timedelta(seconds=1),
        30,
        worker_token="replay-inspect",
    )
    assert replay_claim is not None
    assert replay_claim.attempt_count == 1
    await store.restore_pending(replay_claim.job_id, replay_claim.worker_token)
    while await manager.run_once():
        pass

    rows = await relation_rows(store)
    assert result["success"] is True
    assert result["scheduled_jobs"] == 2
    assert len([row for row in rows if row["relation_type"] == "same_episode"]) == 1
