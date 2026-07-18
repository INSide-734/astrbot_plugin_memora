import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from core.managers.memory_evolution_gate import MemoryEvolutionGate
from core.managers.memory_evolution_manager import MemoryEvolutionManager
from core.managers.memory_evolution_manager import EvolutionProposalRejected
from core.models.memory_evolution import (
    EvolutionProposal,
    MemoryProjectionProposal,
    MemoryRelationProposal,
    MemorySourceRef,
)
from core.processors.memory_consolidator import MemoryConsolidator
from core.storage.memory_evolution_store import MemoryEvolutionStore


UTC = timezone.utc


def source(memory_id: int, scope: str = "private:user-a") -> MemorySourceRef:
    return MemorySourceRef(
        memory_id,
        f"r-{memory_id}",
        scope,
        "shared",
        datetime(2026, 7, 18, tzinfo=UTC),
        f"证据 {memory_id}",
    )


def limits(mode: str = "shadow") -> dict:
    return {
        "enabled": mode != "disabled",
        "mode": mode,
        "trigger_threshold": 0.5,
        "max_pending_jobs": 20,
        "max_attempts": 2,
        "lease_seconds": 30,
        "retry_base_delay_seconds": 1,
        "auto_active_relation_types": ["same_episode", "supports", "related"],
    }


@pytest_asyncio.fixture
async def manager(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    gate = MemoryEvolutionGate(limits())
    consolidator = AsyncMock()
    manager = MemoryEvolutionManager(store, gate, consolidator, limits())
    yield manager
    await manager.stop()
    await store.close()


async def seed_documents(store: MemoryEvolutionStore, *sources: MemorySourceRef) -> None:
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents "
            "(id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, created_at TEXT, updated_at TEXT)"
        )
        await db.executemany(
            "INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    source.memory_id,
                    f"d{source.memory_id}",
                    source.content or "",
                    '{"session_id":"private:user-a"}',
                    source.occurred_at.isoformat(),
                    source.revision_token,
                )
                for source in sources
            ],
        )
        await db.commit()


@pytest.mark.asyncio
async def test_disabled_mode_does_not_enqueue(manager):
    manager.gate = MemoryEvolutionGate(limits("disabled"))
    decision = await manager.schedule_consider(source(17))
    assert decision.reason_code == "mode_disabled"
    assert await manager.store.pending_count() == 0


@pytest.mark.asyncio
async def test_schedule_and_low_impact_relation_become_active(manager):
    manager.consolidator.propose.return_value = EvolutionProposal(
        relations=(MemoryRelationProposal("M1", "M2", "same_episode", 0.8, None, None, None),)
    )
    await seed_documents(manager.store, source(17), source(18))
    await manager.schedule_consider(source(17))
    scheduled = await manager.store.claim_job(datetime.now(UTC), 30)
    assert scheduled is not None
    await manager.store.reject_job(scheduled.job_id, scheduled.worker_token, "test_cleanup")
    from core.models.memory_evolution import JobSpec
    await manager.store.enqueue_job(JobSpec("private:user-a", "bucket", (17, 18), "manual", datetime.now(UTC)))
    assert await manager.run_once() is True
    assert (await manager.store.active_relations_for_seeds([17]))[0].relation_type.value == "same_episode"


@pytest.mark.asyncio
async def test_unknown_alias_is_rejected(manager):
    manager.consolidator.propose.return_value = EvolutionProposal(
        relations=(MemoryRelationProposal("M99", "M1", "related", 0.8, None, None, None),)
    )
    await seed_documents(manager.store, source(17))
    from core.models.memory_evolution import JobSpec
    job = await manager.store.enqueue_job(JobSpec("private:user-a", "bucket", (17,), "unknown-alias", datetime.now(UTC)))
    await manager.run_once()
    assert (await manager.store.get_job(job.job_id)).state.value == "rejected"


@pytest.mark.asyncio
async def test_cancelled_proposal_propagates_and_restores_pending(manager):
    manager.consolidator.propose.side_effect = asyncio.CancelledError()
    await seed_documents(manager.store, source(17))
    from core.models.memory_evolution import JobSpec
    await manager.store.enqueue_job(JobSpec("private:user-a", "bucket", (17,), "cancelled", datetime.now(UTC)))
    with pytest.raises(asyncio.CancelledError):
        await manager.run_once()
    assert await manager.store.pending_count() == 1


@pytest.mark.asyncio
async def test_high_impact_is_candidate_and_privacy_uses_strictest_source(manager):
    private_source = source(17)
    confidential_source = MemorySourceRef(
        18,
        "r-18",
        "private:user-a",
        "confidential",
        datetime(2026, 7, 18, tzinfo=UTC),
        "机密证据",
    )
    proposal = EvolutionProposal(
        relations=(
            MemoryRelationProposal(
                "M1", "M2", "preference_change", 0.9, None, None, None
            ),
        )
    )
    plan = manager._proposal_to_plan(
        proposal,
        [private_source, confidential_source],
    )
    assert plan.relations[0].state.value == "candidate"
    assert plan.relations[0].privacy_level == "confidential"


@pytest.mark.asyncio
async def test_scope_mismatch_is_rejected(manager):
    proposal = EvolutionProposal(
        relations=(
            MemoryRelationProposal("M1", "M2", "related", 0.8, None, None, None),
        )
    )
    with pytest.raises(EvolutionProposalRejected, match="scope_mismatch"):
        manager._proposal_to_plan(
            proposal,
            [source(17, "private:a"), source(18, "private:b")],
        )


@pytest.mark.asyncio
async def test_provider_failure_retries_then_reaches_dead(tmp_path):
    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await seed_documents(store, source(17))
    gate = MemoryEvolutionGate(limits())
    consolidator = AsyncMock()
    consolidator.propose.side_effect = ConnectionError("敏感 provider 原始错误")
    manager = MemoryEvolutionManager(
        store,
        gate,
        consolidator,
        {**limits(), "max_attempts": 1},
    )
    from core.models.memory_evolution import JobSpec

    job = await store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "dead", datetime.now(UTC))
    )
    await manager.run_once()
    assert (await store.get_job(job.job_id)).state.value == "dead"
    await manager.stop()
    await store.close()


@pytest.mark.asyncio
async def test_worker_lifecycle_and_status_snapshot_exclude_sensitive_fields(manager):
    snapshot = manager.get_status_snapshot()
    assert set(snapshot) == {"mode", "worker_running", "max_attempts", "lease_seconds"}
    assert "query" not in snapshot
    assert "prompt" not in snapshot
    assert "content" not in snapshot
    await manager.start()
    assert manager.get_status_snapshot()["worker_running"] is True
    await manager.stop()
    assert manager.get_status_snapshot()["worker_running"] is False


@pytest.mark.asyncio
async def test_direct_process_claim_cancel_restores_pending(manager):
    manager.consolidator.propose.side_effect = asyncio.CancelledError()
    await seed_documents(manager.store, source(17))
    from core.models.memory_evolution import JobSpec
    await manager.store.enqueue_job(JobSpec("private:user-a", "bucket", (17,), "direct-cancelled", datetime.now(UTC)))
    claim = await manager.store.claim_job(datetime.now(UTC), 30)
    assert claim is not None
    with pytest.raises(asyncio.CancelledError):
        await manager.process_claim(claim)
    assert await manager.store.pending_count() == 1


@pytest.mark.asyncio
async def test_projection_sources_pass_scope_validation(manager):
    manager.consolidator.propose.return_value = EvolutionProposal(
        projections=(
            MemoryProjectionProposal(
                "episode_summary",
                ("M1", "M2"),
                None,
                "两条证据属于同一事件。",
                0.8,
                None,
                None,
            ),
        )
    )
    await seed_documents(manager.store, source(17), source(18))
    from core.models.memory_evolution import JobSpec
    await manager.store.enqueue_job(JobSpec("private:user-a", "bucket", (17, 18), "projection", datetime.now(UTC)))
    assert await manager.run_once() is True
    projections = await manager.store.active_projections_for_seeds([17])
    assert projections[0].summary == "两条证据属于同一事件。"
