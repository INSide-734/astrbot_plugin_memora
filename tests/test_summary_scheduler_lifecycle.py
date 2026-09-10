"""总结调度器启动、重复生命周期与取消契约。"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.application.summary_scheduler import SummaryScheduler
from core.features.reflection.domain.summary_models import (
    SummaryTaskSnapshot,
    SummaryWindowContext,
)


def _context(session_id: str, epoch: int, cursor: int) -> SummaryWindowContext:
    """构造启动扫描需要的固定上下文。"""
    snapshot = default_gate_snapshot()
    return SummaryWindowContext(
        session_id=session_id,
        session_epoch=epoch,
        start_seq=cursor,
        end_seq=cursor,
        chat_type="private",
        scope_id=session_id,
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=4,
    )


def _store() -> MagicMock:
    """构造满足启动期最小异步端口的 Store 替身。"""
    store = MagicMock()
    store.set_summary_clock = MagicMock()
    store.recover_expired_claims = AsyncMock(return_value=0)
    store.recover_legacy_pending = AsyncMock(return_value=0)
    store.reconcile_startup_candidates = AsyncMock(return_value=0)
    store.plan_existing_frontiers = AsyncMock(return_value=0)
    store.claim_ready = AsyncMock(return_value=[])
    store.snapshot = AsyncMock(return_value=SummaryTaskSnapshot())
    return store


def _scheduler(store: Any) -> SummaryScheduler:
    """构造带启动上下文工厂的调度器。"""
    return SummaryScheduler(
        store,
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        startup_context_factory=_context,
        retry_poll_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_start_and_close_are_idempotent() -> None:
    """重复 start/close 不应创建第二个循环或泄漏任务。"""
    store = _store()
    scheduler = _scheduler(store)

    await scheduler.start()
    loop_task = scheduler._loop_task
    await scheduler.start()
    await scheduler.close()
    await scheduler.close()

    assert loop_task is not None
    assert loop_task.done()
    assert scheduler._loop_task is None
    store.recover_expired_claims.assert_awaited_once()
    store.recover_legacy_pending.assert_awaited_once()
    store.reconcile_startup_candidates.assert_awaited_once()
    store.plan_existing_frontiers.assert_awaited_once()


@pytest.mark.asyncio
async def test_pause_and_resume_quiesce_then_restart_claim_loop() -> None:
    """一致快照暂停期间无领取循环，恢复后重新扫描并启动。"""
    store = _store()
    scheduler = _scheduler(store)

    await scheduler.start()
    first_loop = scheduler._loop_task
    await scheduler.pause()

    assert first_loop is not None and first_loop.done()
    assert scheduler._loop_task is None
    assert scheduler._accepting_enqueues is False

    await scheduler.resume()
    second_loop = scheduler._loop_task
    assert second_loop is not None and second_loop is not first_loop
    assert scheduler._accepting_enqueues is True
    await scheduler.close()

    assert store.recover_legacy_pending.await_count == 2
    assert store.reconcile_startup_candidates.await_count == 2
    assert store.plan_existing_frontiers.await_count == 2


@pytest.mark.asyncio
async def test_resume_failure_keeps_enqueue_fence_closed() -> None:
    """恢复扫描失败时不得重新接受无法消费的总结窗口。"""
    store = _store()
    scheduler = _scheduler(store)
    await scheduler.start()
    await scheduler.pause()
    store.recover_expired_claims.side_effect = RuntimeError("resume_failed")

    with pytest.raises(RuntimeError, match="summary_recovery_failed"):
        await scheduler.resume()

    assert scheduler._accepting_enqueues is False
    assert scheduler._claiming is False


@pytest.mark.asyncio
async def test_create_task_failure_does_not_publish_claim_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """领取循环创建失败时调度器必须保持未发布状态。"""
    store = _store()
    scheduler = _scheduler(store)

    def fail_create_task(*_args: object, **_kwargs: object) -> Any:
        """模拟运行时无法创建 asyncio task。"""
        raise RuntimeError("task_create_failed")

    monkeypatch.setattr(
        "core.features.reflection.application.summary_scheduler.asyncio.create_task",
        fail_create_task,
    )

    with pytest.raises(RuntimeError, match="task_create_failed"):
        await scheduler.start()

    assert scheduler._loop_task is None
    assert scheduler._claiming is False
    assert scheduler._accepting_enqueues is False


@pytest.mark.asyncio
async def test_claim_loop_survives_transient_store_failure() -> None:
    """领取扫描暂态失败时应保留循环并在下一轮继续恢复。"""
    store = _store()
    first_loop_failure = asyncio.Event()
    recovery_continued = asyncio.Event()
    calls = 0

    async def recover(_now: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            first_loop_failure.set()
            raise RuntimeError("transient_store_failure")
        if calls >= 3:
            recovery_continued.set()
        return 0
        calls += 1
        if calls == 2:
            first_loop_failure.set()
            raise RuntimeError("transient_store_failure")
        return 0

    store.recover_expired_claims = recover
    scheduler = SummaryScheduler(
        store,
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        startup_context_factory=_context,
        retry_poll_seconds=0.001,
    )
    await scheduler.start()
    try:
        await asyncio.wait_for(first_loop_failure.wait(), timeout=1)
        await asyncio.wait_for(recovery_continued.wait(), timeout=1)
        assert scheduler._claiming is True
        assert scheduler._accepting_enqueues is True
        assert scheduler._loop_task is not None
        assert not scheduler._loop_task.done()
    finally:
        await scheduler.close()


@pytest.mark.asyncio
async def test_scheduler_snapshot_falls_back_to_safe_scalars_on_store_error() -> None:
    """Store 快照异常时只返回统一有限标量，不向调用方传播正文。"""
    store = _store()
    store.snapshot = AsyncMock(side_effect=RuntimeError("内部敏感正文"))
    scheduler = _scheduler(store)

    snapshot = await scheduler.snapshot()

    assert snapshot.to_dict() == {
        "queued": 0,
        "running": 0,
        "failed": 0,
        "blocked": 0,
        "unknown": 0,
        "cancelled": 0,
        "abandoned": 0,
        "active_parallelism": 0,
        "target_parallelism": 0,
        "canonical_total": 0,
        "quarantine_total": 0,
        "discard_total": 0,
        "mark_write_total": 0,
        "failed_candidate_total": 0,
        "skipped_idempotent_total": 0,
    }


@pytest.mark.asyncio
async def test_scheduler_start_failure_closes_published_core_resources(
    tmp_path,
) -> None:
    """调度器启动失败时不得留下会话库、引擎或向量库连接。"""
    from core.platform.composition.plugin_initializer import PluginInitializer
    from core.shared.errors import InitializationError

    initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
    initializer._faiss_checker.load_vec_db_class = MagicMock(return_value=MagicMock())
    scheduler = MagicMock()
    scheduler.start = AsyncMock(side_effect=RuntimeError("scheduler_start_failed"))
    scheduler.close = AsyncMock()
    engine = MagicMock()
    engine.close = AsyncMock()
    conversation_store = MagicMock()
    conversation_store.close = AsyncMock()
    conversation_manager = MagicMock(store=conversation_store)
    db = MagicMock()
    db.close = AsyncMock()
    graph_db = MagicMock()
    graph_db.close = AsyncMock()
    identity_runtime = MagicMock()
    identity_runtime.close = AsyncMock()
    initializer._component_factory.build_all = AsyncMock(
        return_value={
            "db": db,
            "graph_db": graph_db,
            "memory_engine": engine,
            "memory_processor": MagicMock(),
            "memory_quarantine_store": MagicMock(),
            "memory_quality_gate": MagicMock(),
            "gate_runtime": MagicMock(),
            "conversation_manager": conversation_manager,
            "identity_runtime": identity_runtime,
            "index_validator": MagicMock(),
            "decay_scheduler": None,
            "injection_decision_store": None,
            "injection_decision_recorder": None,
            "memory_evolution_store": None,
            "memory_evolution_manager": None,
            "realtime_hub": None,
            "summary_scheduler": scheduler,
            "catalog_maintenance_result": {
                "catalog_decision": "ready",
                "safe_baseline": True,
                "reason_code": "catalog_ready",
            },
            "summary_llm_limiter": MagicMock(),
        }
    )
    initializer._create_prompt_protection_service = MagicMock(return_value=None)

    with pytest.raises(InitializationError, match="scheduler_start_failed"):
        await initializer._run_full_init()

    scheduler.close.assert_awaited_once()
    engine.close.assert_awaited_once()
    conversation_store.close.assert_awaited_once()
    graph_db.close.assert_awaited_once()
    db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_backup_failure_resumes_quiesced_summary_scheduler(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备份失败时也必须恢复已经暂停的总结调度器。"""
    from core.features.backup.application.manager import BackupManager

    order: list[str] = []
    scheduler = MagicMock()
    scheduler.pause = AsyncMock(side_effect=lambda: order.append("pause"))
    scheduler.resume = AsyncMock(side_effect=lambda: order.append("resume"))
    manager = BackupManager(str(tmp_path))
    manager.bind_summary_scheduler(scheduler)

    def fail_backup(*_args: object) -> dict[str, object]:
        """记录同步快照阶段并模拟磁盘失败。"""
        order.append("backup")
        raise RuntimeError("backup_failed")

    monkeypatch.setattr(manager, "_create_backup_sync", fail_backup)

    with pytest.raises(RuntimeError, match="backup_failed"):
        await manager.create_backup()

    assert order == ["pause", "backup", "resume"]


@pytest.mark.asyncio
async def test_cancelled_backup_waits_for_snapshot_thread_before_resume(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消备份时不得在同步 SQLite 快照仍运行时恢复总结写入。"""
    from core.features.backup.application.manager import BackupManager

    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []
    scheduler = MagicMock()
    scheduler.pause = AsyncMock(side_effect=lambda: order.append("pause"))
    scheduler.resume = AsyncMock(side_effect=lambda: order.append("resume"))
    manager = BackupManager(str(tmp_path))
    manager.bind_summary_scheduler(scheduler)

    def blocking_backup(*_args: object) -> dict[str, object]:
        """阻塞同步快照线程，直到测试显式释放。"""
        order.append("backup-start")
        entered.set()
        release.wait()
        order.append("backup-end")
        return {"status": "ready"}

    monkeypatch.setattr(manager, "_create_backup_sync", blocking_backup)
    task = asyncio.create_task(manager.create_backup())
    await asyncio.to_thread(entered.wait)
    task.cancel()
    turn_completed = asyncio.Event()
    asyncio.get_running_loop().call_soon(turn_completed.set)
    await turn_completed.wait()

    scheduler.resume.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert order == ["pause", "backup-start", "backup-end", "resume"]
