"""总结调度器的入队、并发上限与公平领取契约。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.application.summary_scheduler import SummaryScheduler
from core.features.reflection.domain.summary_models import (
    ClaimedJob,
    SummaryEnqueueResult,
    SummaryJob,
    SummaryJobStatus,
    SummaryTaskSnapshot,
    SummaryWindowContext,
    WindowOutcome,
)
from core.shared.contracts.conversation import Message


def _context(session_id: str = "s") -> SummaryWindowContext:
    """构造最小可入队的固定上下文。"""
    snapshot = default_gate_snapshot()
    return SummaryWindowContext(
        session_id=session_id,
        session_epoch=1,
        start_seq=0,
        end_seq=0,
        chat_type="private",
        scope_id=session_id,
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=4,
    )


def _message(session_id: str, index: int) -> Message:
    """构造可由真实 ConversationStore 持久化的测试消息。"""
    return Message.from_dict(
        {
            "id": 0,
            "session_id": session_id,
            "role": "user",
            "content": f"消息-{index}",
            "sender_id": "user-1",
            "group_id": None,
            "platform": "test",
            "timestamp": float(index),
            "metadata": {},
        }
    )


def _claim(session_id: str, start: int, job_id: str) -> ClaimedJob:
    """构造用于公平排序断言的 claim。"""
    job = SummaryJob(
        job_id=job_id,
        session_id=session_id,
        session_epoch=1,
        start_seq=start,
        end_seq=start + 4,
        expected_count=4,
        source_digest="digest",
        status=SummaryJobStatus.RUNNING,
        chat_type="private",
        scope_id=session_id,
        gate_revision="revision",
        gate_snapshot_json='{"enabled":true}',
        created_at=float(start),
        updated_at=float(start),
    )
    return ClaimedJob(job, f"token-{job_id}", "scheduler", 200.0, 1)


def _scheduler(store: Any) -> SummaryScheduler:
    """构造不启动 worker 的调度器替身。"""
    return SummaryScheduler(
        store,
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        max_parallel_summary_tasks=4,
        max_parallel_summary_tasks_per_session=2,
    )


@pytest.mark.asyncio
async def test_manual_and_automatic_enqueue_share_planner() -> None:
    """自动和手动入口都应只调用同一个固定窗口 planner。"""
    store = MagicMock()
    store.plan_and_enqueue_windows = AsyncMock(
        side_effect=[
            SummaryEnqueueResult(True, queued=1),
            SummaryEnqueueResult(True, queued=1),
        ]
    )
    store.snapshot = AsyncMock(return_value=SummaryTaskSnapshot())
    scheduler = _scheduler(store)

    manual = await scheduler.enqueue_manual(_context(), 4)
    automatic = await scheduler.enqueue_automatic(_context(), 4)

    assert manual.accepted is True
    assert automatic.accepted is True
    assert [
        call.args[0].triggered_by
        for call in store.plan_and_enqueue_windows.await_args_list
    ] == [
        "manual",
        "automatic",
    ]


@pytest.mark.asyncio
async def test_scheduler_calculates_target_from_ready_and_active_snapshot() -> None:
    """目标并发不得超过全局上限，并与实际 active 分开计算。"""
    store = MagicMock()
    store.snapshot = AsyncMock(
        return_value=SummaryTaskSnapshot(queued=3, running=1, active_parallelism=1)
    )
    scheduler = _scheduler(store)

    snapshot = await scheduler.snapshot()

    assert snapshot.active_parallelism == 1
    assert snapshot.target_parallelism == 4


def test_round_robin_keeps_earliest_job_per_session() -> None:
    """每轮每个会话只保留最早窗口，并从上次会话后继续轮转。"""
    scheduler = _scheduler(MagicMock())
    scheduler._round_robin_cursor = "a"

    ordered, duplicates = scheduler._round_robin_order(
        [
            _claim("b", 0, "b0"),
            _claim("a", 4, "a4"),
            _claim("a", 0, "a0"),
            _claim("c", 0, "c0"),
        ]
    )

    assert [claim.session_id for claim in ordered] == ["b", "c", "a"]
    assert [claim.job_id for claim in ordered] == ["b0", "c0", "a0"]
    assert [claim.job_id for claim in duplicates] == ["a4"]


@pytest.mark.asyncio
async def test_scheduler_runs_two_sessions_concurrently(tmp_db_path: str) -> None:
    """两个可执行会话必须同时占用两个 worker 槽位。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    try:
        for session_id in ("a", "b"):
            for index in range(4):
                await store.add_message(_message(session_id, index))
            assert (
                await store.plan_and_enqueue_windows(_context(session_id), 4)
            ).queued == 1

        entered: set[str] = set()
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def execute(claim: ClaimedJob) -> WindowOutcome:
            """记录真实调度器领取的会话并等待统一释放。"""
            entered.add(claim.session_id)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()
            return WindowOutcome(can_advance=True)

        def startup_context(
            session_id: str, epoch: int, cursor: int
        ) -> SummaryWindowContext:
            """为启动扫描恢复当前会话的可信固定上下文。"""
            return replace(
                _context(session_id),
                session_epoch=epoch,
                start_seq=cursor,
                end_seq=cursor,
            )

        scheduler = SummaryScheduler(
            cast(Any, store),
            MagicMock(),
            None,
            MagicMock(),
            MagicMock(),
            max_parallel_summary_tasks=2,
            max_parallel_summary_tasks_per_session=1,
            startup_context_factory=startup_context,
        )
        scheduler._worker.execute = AsyncMock(side_effect=execute)
        await scheduler.start()
        try:
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            assert entered == {"a", "b"}
            assert scheduler.active_parallelism == 2
        finally:
            release.set()
            await scheduler.close()
    finally:
        await store.close()
