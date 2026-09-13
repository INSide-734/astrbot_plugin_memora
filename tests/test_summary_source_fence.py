"""总结来源 fence 到 canonical 写入入口的回归契约。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.domain.summary_models import (
    ClaimedJob,
    SummaryWindowContext,
    WindowOutcome,
)
from core.shared.contracts.conversation import Message
from core.shared.summary_source_fence import SummarySourceFence


def _fence() -> SummarySourceFence:
    """构造不含正文的固定总结来源 fence。"""

    return SummarySourceFence(
        job_id="job-1",
        session_id="session-1",
        session_epoch=1,
        start_seq=0,
        end_seq=2,
        expected_count=2,
        source_digest="digest",
        worker_generation=1,
        claim_token="claim-token",
    )


@pytest.mark.asyncio
async def test_fenced_write_rejects_before_canonical_when_source_is_invalid() -> None:
    """来源已失效时不得开始可见 canonical 写入。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine._add_memory_unchecked = AsyncMock(return_value=17)  # type: ignore[method-assign]
    engine.set_summary_source_validator(AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="summary_source_fenced"):
        await engine.add_memory(
            "候选正文",
            metadata={"idempotency_key": "summary-key"},
            source_fence=_fence(),
        )

    # type: ignore[attr-defined]
    engine._add_memory_unchecked.assert_not_awaited()


@pytest.mark.asyncio
async def test_fenced_write_is_left_nonrecallable_when_source_expires_mid_write() -> (
    None
):
    """写入途中失去 fence 时，canonical 只能保留为不可召回 orphan。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine._add_memory_unchecked = AsyncMock(return_value=17)  # type: ignore[method-assign]
    # type: ignore[method-assign]
    engine._set_summary_source_orphan = AsyncMock()
    engine.set_summary_source_validator(AsyncMock(side_effect=(True, False)))

    with pytest.raises(RuntimeError, match="summary_source_fenced"):
        await engine.add_memory(
            "候选正文",
            metadata={"idempotency_key": "summary-key"},
            source_fence=_fence(),
        )

    # type: ignore[attr-defined]
    engine._add_memory_unchecked.assert_awaited_once()
    engine._set_summary_source_orphan.assert_awaited_once_with(17, True)  # type: ignore[attr-defined]


def _message(session_id: str, index: int) -> Message:
    """构造具备稳定序号的最小测试消息。"""

    return Message.from_dict(
        {
            "id": 0,
            "session_id": session_id,
            "role": "user",
            "content": f"消息-{index}",
            "sender_id": "user-1",
            "sender_name": None,
            "group_id": None,
            "platform": "test",
            "timestamp": float(index),
            "metadata": {},
        }
    )


async def _running_claim(tmp_db_path: str) -> tuple[ConversationStore, ClaimedJob]:
    """准备一个可执行的 running claim。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    for index in range(2):
        await store.add_message(_message("fence-session", index))
    snapshot = default_gate_snapshot()
    context = SummaryWindowContext(
        session_id="fence-session",
        session_epoch=1,
        start_seq=0,
        end_seq=0,
        chat_type="private",
        scope_id="fence-session",
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=2,
    )
    assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
    claims = await store.claim_ready(100.0, "fence-scheduler", 1)
    assert len(claims) == 1
    return store, claims[0]


class _BarrierLock(asyncio.Lock):
    """在获取锁前发出事件，避免用时间推测并发顺序。"""

    def __init__(self) -> None:
        super().__init__()
        self.acquire_requested = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        self.acquire_requested.set()
        return await super().acquire()


@pytest.mark.asyncio
async def test_legal_other_session_transaction_waits_for_store_lock(
    tmp_db_path: str,
) -> None:
    """另一会话持有合法事务时 runner 等待，释放后操作与后续提交均成功。"""
    store, claim = await _running_claim(tmp_db_path)
    runner = None
    try:
        lock = _BarrierLock()
        store._write_lock = lock
        await lock.acquire()
        lock.acquire_requested.clear()
        assert store.connection is not None
        await store.connection.execute("BEGIN IMMEDIATE")
        operation_called = asyncio.Event()

        async def operation() -> str:
            operation_called.set()
            assert store.connection is not None
            assert store.connection.in_transaction is False
            return "ok"

        runner = asyncio.create_task(store.run_claim_side_effect(claim, operation))
        await lock.acquire_requested.wait()
        assert operation_called.is_set() is False
        await store.connection.commit()
        lock.release()
        assert await runner == "ok"

        committed = await store.commit_window(claim, WindowOutcome(can_advance=True))
        assert committed.accepted is True
        assert committed.status.value == "completed"
    finally:
        if runner is not None and not runner.done():
            runner.cancel()
        if store.connection is not None and store.connection.in_transaction:
            await store.connection.rollback()
        await store.close()


@pytest.mark.asyncio
async def test_leaked_transaction_rejects_without_running_operation(
    tmp_db_path: str,
) -> None:
    """当前连接泄漏事务时 runner 拒绝且不执行外部副作用。"""
    store, claim = await _running_claim(tmp_db_path)
    try:
        assert store.connection is not None
        await store.connection.execute("BEGIN")
        operation = AsyncMock(return_value="unexpected")
        with pytest.raises(RuntimeError, match="summary_store_transaction_active"):
            await store.run_claim_side_effect(claim, operation)
        operation.assert_not_awaited()
        assert store.connection.in_transaction is True
        await store.connection.rollback()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_claim_invalidated_during_callback_rejects_after_callback(
    tmp_db_path: str,
) -> None:
    """回调期间 claim 被另一连接失效后，runner 不返回副作用结果。"""
    store, claim = await _running_claim(tmp_db_path)
    other = ConversationStore(tmp_db_path)
    runner = None
    try:
        await other.initialize()
        other.set_summary_clock(lambda: 100.0)
        callback_started = asyncio.Event()
        release_callback = asyncio.Event()

        async def operation() -> str:
            callback_started.set()
            await release_callback.wait()
            return "must-not-escape"

        runner = asyncio.create_task(store.run_claim_side_effect(claim, operation))
        await callback_started.wait()
        assert other.connection is not None
        await other.connection.execute(
            "UPDATE summary_jobs SET claim_token=? WHERE job_id=?",
            ("stale-token", claim.job_id),
        )
        await other.connection.commit()
        release_callback.set()
        with pytest.raises(RuntimeError, match="claim_lost"):
            await runner
        assert store._summary_source_lock_for(claim.session_id).locked() is False
        assert store._write_lock.locked() is False
    finally:
        if runner is not None and not runner.done():
            runner.cancel()
        await other.close()
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_callback_releases_source_and_store_locks(
    tmp_db_path: str,
) -> None:
    """取消外部回调必须穿透并释放来源锁与 Store 写锁。"""
    store, claim = await _running_claim(tmp_db_path)
    try:
        callback_started = asyncio.Event()
        hold_callback = asyncio.Event()

        async def operation() -> None:
            callback_started.set()
            await hold_callback.wait()

        runner = asyncio.create_task(store.run_claim_side_effect(claim, operation))
        await callback_started.wait()
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
        assert store._summary_source_lock_for(claim.session_id).locked() is False
        assert store._write_lock.locked() is False
        assert store.connection is not None
        assert store.connection.in_transaction is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_conversation_store_validates_only_matching_running_claim(
    tmp_db_path: str,
) -> None:
    """持久化 fence 必须绑定同一运行中 claim，令牌变化立即拒绝。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("session-1", index))
        snapshot = default_gate_snapshot()
        context = SummaryWindowContext(
            session_id="session-1",
            session_epoch=1,
            start_seq=0,
            end_seq=0,
            chat_type="private",
            scope_id="session-1",
            gate_revision=snapshot.revision,
            gate_snapshot_json=gate_snapshot_to_json(snapshot),
            window_size=2,
            scope_key="scope-1",
            privacy_level="shared",
            resolver_revision="resolver-1",
            scope_provenance_complete=True,
        )
        assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        claim = claims[0]
        fence = SummarySourceFence(
            job_id=claim.job_id,
            session_id=claim.session_id,
            session_epoch=claim.session_epoch,
            start_seq=claim.start_seq,
            end_seq=claim.end_seq,
            expected_count=claim.expected_count,
            source_digest=claim.source_digest,
            worker_generation=claim.worker_generation,
            claim_token=claim.claim_token,
            scope_key=claim.scope_key,
            privacy_level=claim.privacy_level,
            resolver_revision=claim.resolver_revision,
            scope_provenance_complete=claim.scope_provenance_complete,
        )

        assert await store.summary_source_fence_is_active(fence)
        assert not await store.summary_source_fence_is_active(
            replace(fence, claim_token="other-token")
        )
    finally:
        await store.close()
