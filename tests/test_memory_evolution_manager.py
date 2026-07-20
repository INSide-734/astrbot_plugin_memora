import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from core.managers.memory_evolution_gate import MemoryEvolutionGate
from core.managers.memory_evolution_manager import (
    EvolutionLeaseLost,
    EvolutionProposalRejected,
    MemoryEvolutionManager,
)
from core.models.memory_evolution import (
    EvolutionProposal,
    JobState,
    MemoryProjectionProposal,
    MemoryRelationProposal,
    MemorySourceRef,
)
from core.processors.memory_consolidator import MemoryConsolidator
from core.storage.memory_evolution_store import MemoryEvolutionStore


UTC = timezone.utc


def source(
    memory_id: int,
    scope: str = "private:user-a",
    revision: str | None = None,
) -> MemorySourceRef:
    return MemorySourceRef(
        memory_id,
        revision or f"r-{memory_id}",
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
    manager.consolidator.propose.assert_not_awaited()


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
async def test_schedule_deduplicates_same_revision_but_keeps_new_revision(manager):
    """burst 中同 revision 去重，新 revision 必须保留为独立 job。"""

    await manager.schedule_consider(source(17, revision="r-17-a"))
    await manager.schedule_consider(source(17, revision="r-17-a"))
    assert await manager.store.pending_count() == 1

    await manager.schedule_consider(source(17, revision="r-17-b"))
    assert await manager.store.pending_count() == 2


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
async def test_non_structured_proposal_is_rejected_without_canonical_mutation(manager):
    """不属于 EvolutionProposal 的返回值不能进入派生写入计划。"""

    manager.consolidator.propose.return_value = {
        "canonical_updates": [{"memory_id": 17, "content": "覆盖正文"}]
    }
    await seed_documents(manager.store, source(17))
    from core.models.memory_evolution import JobSpec

    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "proposal-schema", datetime.now(UTC))
    )

    assert await manager.run_once() is True
    stored = await manager.store.get_job(job.job_id)
    assert stored is not None
    assert stored.state is JobState.REJECTED
    assert manager.get_status_snapshot()["reason_codes"]["proposal_schema_invalid"] == 1


@pytest.mark.asyncio
async def test_oversized_proposal_is_rejected_instead_of_truncated(manager):
    """超过 manager 上限的 proposal 必须拒绝，不能静默丢弃尾部。"""

    manager.candidate_limit = 1
    proposal = EvolutionProposal(
        relations=(
            MemoryRelationProposal("M1", "M2", "related", 0.8, None, None, None),
            MemoryRelationProposal("M1", "M3", "related", 0.8, None, None, None),
        )
    )

    with pytest.raises(EvolutionProposalRejected, match="proposal_limit_exceeded"):
        manager._proposal_to_plan(proposal, [source(17), source(18), source(19)])


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
async def test_high_impact_cannot_become_active_when_review_flag_is_disabled(manager):
    """高影响关系不受错误配置影响，始终保持候选状态等待复核。"""

    manager.require_review_for_high_impact = False
    proposal = EvolutionProposal(
        relations=(
            MemoryRelationProposal("M1", "M2", "contradicts", 0.99, None, None, None),
        )
    )

    plan = manager._proposal_to_plan(proposal, [source(17), source(18)])

    assert plan.relations[0].state.value == "candidate"


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
async def test_low_confidence_low_impact_relation_stays_candidate(manager):
    proposal = EvolutionProposal(
        relations=(
            MemoryRelationProposal("M1", "M2", "same_episode", 0.1, None, None, None),
        )
    )
    plan = manager._proposal_to_plan(proposal, [source(17), source(18)])
    assert plan.relations[0].state.value == "candidate"


@pytest.mark.asyncio
async def test_three_node_cycle_is_rejected(manager):
    proposal = EvolutionProposal(
        relations=(
            MemoryRelationProposal("M1", "M2", "causes", 0.9, None, None, None),
            MemoryRelationProposal("M2", "M3", "causes", 0.9, None, None, None),
            MemoryRelationProposal("M3", "M1", "causes", 0.9, None, None, None),
        )
    )
    with pytest.raises(EvolutionProposalRejected, match="duplicate_or_cycle"):
        manager._proposal_to_plan(proposal, [source(17), source(18), source(19)])


@pytest.mark.asyncio
async def test_conflict_projection_requires_both_conflict_sides(manager):
    proposal = EvolutionProposal(
        projections=(
            MemoryProjectionProposal(
                "conflict_set",
                ("M1", "M2"),
                None,
                "两条证据存在冲突。",
                0.9,
                None,
                None,
            ),
        )
    )
    with pytest.raises(EvolutionProposalRejected, match="conflict_source_roles"):
        manager._proposal_to_plan(proposal, [source(17), source(18)])


@pytest.mark.asyncio
async def test_source_revision_change_is_rejected_before_apply(manager):
    from core.models.memory_evolution import JobSpec

    manager.consolidator.propose.return_value = EvolutionProposal()
    manager.store.load_sources = AsyncMock(
        side_effect=[[source(17)], [source(17, revision="r-17-new")]]
    )
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "revision-race", datetime.now(UTC))
    )
    assert await manager.run_once() is True
    stored = await manager.store.get_job(job.job_id)
    assert stored is not None
    assert stored.state is JobState.INVALIDATED
    assert manager.store.load_sources.await_count == 2


@pytest.mark.asyncio
async def test_job_revision_stale_before_claim_is_invalidated(manager):
    """入队后 canonical revision 改变时不得按新正文执行旧 job。"""

    from core.models.memory_evolution import JobSpec

    await seed_documents(manager.store, source(17, revision="r-17-new"))
    job = await manager.store.enqueue_job(
        JobSpec(
            "private:user-a",
            "bucket",
            (17,),
            "stale-before-claim",
            datetime.now(UTC),
            source_revisions={17: "r-17-old"},
        )
    )

    assert await manager.run_once() is True
    stored = await manager.store.get_job(job.job_id)
    assert stored is not None
    assert stored.state is JobState.INVALIDATED
    manager.consolidator.propose.assert_not_awaited()


@pytest.mark.asyncio
async def test_processing_renews_lease(manager):
    from core.models.memory_evolution import JobSpec

    manager.lease_seconds = 1
    await seed_documents(manager.store, source(17))
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "lease-renewal", datetime.now(UTC))
    )
    claim = await manager.store.claim_job(datetime.now(UTC), manager.lease_seconds)
    assert claim is not None
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_propose(_sources):
        started.set()
        await release.wait()
        return EvolutionProposal()

    manager.consolidator.propose.side_effect = slow_propose
    manager.store.renew_lease = AsyncMock(return_value=True)
    task = asyncio.create_task(manager.process_claim(claim))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.45)
    release.set()
    await task
    assert manager.store.renew_lease.await_count >= 1
    assert (await manager.store.get_job(job.job_id)).state is JobState.COMPLETED


@pytest.mark.asyncio
async def test_lost_lease_before_apply_does_not_write_derived_plan(manager):
    """最终 ownership 检查失败时不得写 relation/projection 或完成 job。"""

    from core.models.memory_evolution import JobSpec

    await seed_documents(manager.store, source(17))
    manager.consolidator.propose.return_value = EvolutionProposal()
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "lease-lost", datetime.now(UTC))
    )
    claim = await manager.store.claim_job(datetime.now(UTC), 30)
    assert claim is not None
    manager.store.renew_lease = AsyncMock(return_value=False)
    manager.store.apply_derived_plan = AsyncMock()
    manager.store.complete_job = AsyncMock()

    with pytest.raises(EvolutionLeaseLost, match="job_lease_lost"):
        await manager.process_claim(claim)

    manager.store.apply_derived_plan.assert_not_awaited()
    manager.store.complete_job.assert_not_awaited()
    assert (await manager.store.get_job(job.job_id)).state is JobState.PROCESSING


@pytest.mark.asyncio
async def test_retryable_provider_failure_enters_retry_wait(manager, monkeypatch):
    """临时 Provider 错误按指数退避进入 retry_wait。"""

    from core.models.memory_evolution import JobSpec

    manager.max_attempts = 2
    manager.retry_base_delay_seconds = 2
    monkeypatch.setattr(
        "core.managers.memory_evolution_manager.random.uniform",
        lambda _start, _end: 0.0,
    )
    await seed_documents(manager.store, source(17))
    manager.consolidator.propose.side_effect = ConnectionError("临时不可用")
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "retry-wait", datetime.now(UTC))
    )
    before = datetime.now(UTC)

    assert await manager.run_once() is True
    stored = await manager.store.get_job(job.job_id)

    assert stored is not None
    assert stored.state is JobState.RETRY_WAIT
    assert stored.not_before >= before + timedelta(seconds=2)
    assert manager.get_status_snapshot()["reason_codes"]["provider_unavailable"] == 1


@pytest.mark.asyncio
async def test_non_retryable_proposal_error_goes_directly_to_dead(manager):
    """确定性的 proposal 解析错误不应浪费 provider 重试预算。"""

    from core.models.memory_evolution import JobSpec

    manager.max_attempts = 5
    await seed_documents(manager.store, source(17))
    manager.consolidator.propose.side_effect = ValueError("结构化输出不合法")
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "proposal-dead", datetime.now(UTC))
    )

    assert await manager.run_once() is True
    stored = await manager.store.get_job(job.job_id)

    assert stored is not None
    assert stored.state is JobState.DEAD
    snapshot = manager.get_status_snapshot()
    assert snapshot["dead"] == 1
    assert snapshot["retry"] == 0
    assert snapshot["reason_codes"]["proposal_invalid"] == 1


@pytest.mark.asyncio
async def test_store_source_race_invalidates_job_instead_of_retrying(manager):
    """最终派生事务发现 source revision 竞态时直接失效旧 job。"""

    from core.models.memory_evolution import JobSpec

    await seed_documents(manager.store, source(17))
    manager.consolidator.propose.return_value = EvolutionProposal()
    manager.store.apply_derived_plan = AsyncMock(
        side_effect=ValueError("source_revision_mismatch")
    )
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "store-race", datetime.now(UTC))
    )

    assert await manager.run_once() is True
    stored = await manager.store.get_job(job.job_id)

    assert stored is not None
    assert stored.state is JobState.INVALIDATED
    snapshot = manager.get_status_snapshot()
    assert snapshot["retry"] == 0
    assert snapshot["reason_codes"]["source_revision_mismatch"] == 1


@pytest.mark.asyncio
async def test_start_recovers_expired_lease_before_worker_loop(manager):
    """启动 worker 前必须先恢复过期 processing job。"""

    from core.models.memory_evolution import JobSpec

    old_now = datetime.now(UTC) - timedelta(minutes=5)
    job = await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "recover-start", old_now)
    )
    assert await manager.store.claim_job(old_now, 1) is not None
    original_recover = manager.store.recover_expired_leases
    manager.store.recover_expired_leases = AsyncMock(wraps=original_recover)
    worker_started = asyncio.Event()

    async def idle_worker() -> None:
        """阻塞测试 worker，避免恢复后的 job 被立即消费。"""

        worker_started.set()
        await asyncio.Future()

    manager._worker_loop = idle_worker
    await manager.start()
    await asyncio.wait_for(worker_started.wait(), timeout=1)

    manager.store.recover_expired_leases.assert_awaited_once()
    assert (await manager.store.get_job(job.job_id)).state is JobState.PENDING
    await manager.stop()


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
    assert set(snapshot) == {
        "mode",
        "state_counts",
        "queue_lag_seconds",
        "type_counts",
        "accepted",
        "rejected",
        "retry",
        "dead",
        "reason_codes",
        "token_totals",
        "latency_buckets",
    }
    assert "query" not in snapshot
    assert "prompt" not in snapshot
    assert "content" not in snapshot
    await manager.start()
    assert manager._worker_task is not None
    await manager.stop()
    assert manager._worker_task is None


@pytest.mark.asyncio
async def test_worker_poll_error_isolated_and_loop_continues(manager):
    """claim/poll 普通错误只降级当前轮次，不能杀死持久化 worker。"""

    manager.poll_interval_seconds = 0.01
    second_call = asyncio.Event()
    call_count = 0

    async def flaky_run_once() -> bool:
        """首轮失败，次轮阻塞等待测试取消。"""

        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("database locked")
        second_call.set()
        await asyncio.Future()
        return False

    manager.run_once = flaky_run_once
    await manager.start()
    await asyncio.wait_for(second_call.wait(), timeout=1)

    assert manager.get_status_snapshot()["reason_codes"]["worker_poll_failed"] == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_start_recovery_failure_degrades_but_starts_worker(manager):
    """过期 lease 恢复暂时失败时仍启动 worker，等待后续 poll 恢复。"""

    manager.store.recover_expired_leases = AsyncMock(
        side_effect=RuntimeError("database locked")
    )
    worker_started = asyncio.Event()

    async def idle_worker() -> None:
        """标记 worker 已启动后等待取消。"""

        worker_started.set()
        await asyncio.Future()

    manager._worker_loop = idle_worker
    await manager.start()
    await asyncio.wait_for(worker_started.wait(), timeout=1)

    assert manager.get_status_snapshot()["reason_codes"]["lease_recovery_failed"] == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_stop_propagates_caller_cancellation(manager):
    """stop 只吞自身发出的 worker 取消，不能吞调用方取消。"""

    first_cancel_seen = asyncio.Event()

    async def stubborn_worker() -> None:
        """吞掉第一次内部取消，以便模拟调用方随后取消 stop。"""

        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            first_cancel_seen.set()
            await asyncio.Future()

    manager._worker_task = asyncio.create_task(stubborn_worker())
    stop_task = asyncio.create_task(manager.stop())
    await asyncio.wait_for(first_cancel_seen.wait(), timeout=1)
    stop_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stop_task


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
async def test_cancel_restore_failure_does_not_replace_cancelled_error(manager):
    """恢复 pending 失败只能记录原因，原始取消必须继续传播。"""

    from core.models.memory_evolution import JobSpec

    manager.consolidator.propose.side_effect = asyncio.CancelledError()
    await seed_documents(manager.store, source(17))
    await manager.store.enqueue_job(
        JobSpec("private:user-a", "bucket", (17,), "cancel-restore-failed", datetime.now(UTC))
    )
    claim = await manager.store.claim_job(datetime.now(UTC), 30)
    assert claim is not None
    manager.store.restore_pending = AsyncMock(side_effect=RuntimeError("database locked"))

    with pytest.raises(asyncio.CancelledError):
        await manager.process_claim(claim)

    assert manager.get_status_snapshot()["reason_codes"]["cancel_restore_failed"] == 1


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
