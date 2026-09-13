"""F3 UNKNOWN 归因与隔离副作用恢复回归。"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.quality.infrastructure.quarantine_store import (
    MemoryQuarantineStore,
)
from core.features.reflection.application.summary_worker import SummaryWorker
from core.features.reflection.domain.summary_models import (
    CandidateIntent,
    SummaryReasonCode,
    SummaryWindowContext,
)
from core.shared.contracts.conversation import Message


def _context(session_id: str) -> SummaryWindowContext:
    """构造可规划的最小总结上下文。"""
    snapshot = default_gate_snapshot()
    return SummaryWindowContext(
        session_id=session_id,
        session_epoch=1,
        start_seq=0,
        chat_type="private",
        scope_id=session_id,
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=2,
    )


def _message(session_id: str, index: int) -> Message:
    """构造临时库中的稳定用户消息。"""
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


async def _prepare_claim(tmp_path, *, key: str = "summary-candidate"):
    """准备 writing ledger、来源窗口和两个独立临时 Store。"""
    conversation_path = tmp_path / "conversations.db"
    quarantine_path = tmp_path / "quarantine.db"
    store = ConversationStore(str(conversation_path))
    quarantine = MemoryQuarantineStore(quarantine_path)
    await store.initialize()
    await quarantine.initialize()
    store.set_summary_clock(lambda: 100.0)
    session_id = "f3-session"
    for index in range(2):
        await store.add_message(_message(session_id, index))
    assert (await store.plan_and_enqueue_windows(_context(session_id), 2)).queued == 1
    claims = await store.claim_ready(100.0, "f3-scheduler", 1)
    assert len(claims) == 1
    claim = claims[0]
    content = "用户喜欢咖啡。"
    intent = CandidateIntent(
        slot=0,
        content_digest=hashlib.sha256(content.encode()).hexdigest(),
        idempotency_key=key,
        slot_key="store-owned",
    )
    assert await store.begin_candidate_intents(claim, (intent,))
    assert await store.begin_candidate_write(claim, intent)
    return store, quarantine, conversation_path, claim, intent, content


async def _stage_quarantine(quarantine, claim, key: str, content: str) -> None:
    """写入与 claim 来源严格对应的 pending 隔离候选。"""
    await quarantine.stage_candidate(
        candidate_key=key,
        reason_codes=["summary_quality_low"],
        content=content,
        metadata={"key_facts": [content]},
        importance=0.7,
        session_id=claim.session_id,
        persona_id=claim.persona_id,
        source_window={
            "session_id": claim.session_id,
            "start_seq": claim.start_seq,
            "end_seq": claim.end_seq,
            "expected_count": claim.expected_count,
            "session_epoch": claim.session_epoch,
            "source_digest": claim.source_digest,
        },
        is_group_chat=False,
    )


@pytest.mark.asyncio
async def test_gate_exception_persists_safe_exception_type(tmp_path) -> None:
    """门禁异常仍保留 UNKNOWN，并只保存闭集异常类型。"""
    store, _, _, claim, intent, _ = await _prepare_claim(tmp_path)
    try:
        worker = cast(Any, object.__new__(SummaryWorker))
        worker._quality_gate = type(
            "Gate",
            (),
            {
                "route_candidate": AsyncMock(
                    side_effect=RuntimeError("sensitive gate detail")
                )
            },
        )()
        worker._fixed_snapshot_kwargs = SummaryWorker._fixed_snapshot_kwargs
        worker._claim_is_active = AsyncMock(return_value=True)

        async def run_side_effect(claim: Any, operation: Any) -> object:
            return await operation()

        worker.run_claim_side_effect = run_side_effect
        worker._is_group_chat = lambda claim: False
        candidate = {
            "content": "用户喜欢咖啡。",
            "metadata": {"idempotency_key": intent.idempotency_key},
        }
        _, reason, exception_type = await worker._route_quality(
            claim, [candidate], {}, {}
        )
        assert reason is SummaryReasonCode.LEDGER_UNRESOLVED
        assert exception_type == "RuntimeError"
        outcome = worker._unknown_outcome(
            (intent,),
            stage="quality_gate",
            reason_code=reason,
            exception_type="RuntimeError: sensitive gate detail",
        )
        assert outcome.exception_type == "unknown"
        committed = await store.commit_window(claim, outcome)
        assert committed.status.value == "unknown"
        assert store.connection is not None
        row = await (
            await store.connection.execute(
                "SELECT status,reason_code,exception_type FROM summary_jobs"
            )
        ).fetchone()
        assert row is not None
        assert tuple(row) == ("unknown", "ledger_unresolved", "unknown")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pending_quarantine_recovers_without_canonical_write_or_duplicate(
    tmp_path,
) -> None:
    """pending 隔离已落库而 ledger 仍 writing 时可严格收束并幂等重启。"""
    store, quarantine, conversation_path, claim, intent, content = await _prepare_claim(
        tmp_path
    )
    await _stage_quarantine(quarantine, claim, intent.idempotency_key, content)
    await store.close()

    reopened = ConversationStore(str(conversation_path))
    await reopened.initialize()
    reopened.set_summary_clock(lambda: 100.0)
    canonical_lookup = AsyncMock(return_value=None)
    query_states: list[bool] = []

    async def lookup(key: str):
        assert reopened.connection is not None
        query_states.append(bool(reopened.connection.in_transaction))
        return await quarantine.find_quarantine_candidate_by_key(key)

    reopened.set_summary_canonical_owner_lookup(canonical_lookup)
    reopened.set_summary_quarantine_candidate_lookup(lookup)
    try:
        assert await reopened.reconcile_startup_candidates() == 1
        assert query_states == [False]
        assert canonical_lookup.await_count == 1
        assert reopened.connection is not None
        job = await (
            await reopened.connection.execute(
                "SELECT status,quarantine_count,exception_type FROM summary_jobs"
            )
        ).fetchone()
        candidate = await (
            await reopened.connection.execute(
                "SELECT status,disposition,canonical_id FROM summary_job_candidates"
            )
        ).fetchone()
        cursor = await (
            await reopened.connection.execute(
                "SELECT cursor_seq FROM session_epochs WHERE session_id=?",
                (claim.session_id,),
            )
        ).fetchone()
        assert job is not None and tuple(job) == ("completed", 1, None)
        assert candidate is not None and tuple(candidate) == (
            "committed",
            "quarantined",
            None,
        )
        assert cursor is not None and tuple(cursor) == (claim.end_seq,)
        assert await reopened.reconcile_startup_candidates() == 0
        assert canonical_lookup.await_count == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_absent_quarantine_evidence_keeps_unknown_cursor_blocked(
    tmp_path,
) -> None:
    """隔离库没有同 key 证据时不得猜测完成或推进游标。"""
    store, _, _, claim, _, _ = await _prepare_claim(tmp_path)
    canonical_lookup = AsyncMock(return_value=None)
    quarantine_lookup = AsyncMock(return_value=None)
    store.set_summary_canonical_owner_lookup(canonical_lookup)
    store.set_summary_quarantine_candidate_lookup(quarantine_lookup)
    try:
        assert await store.reconcile_startup_candidates() == 1
        assert await store.get_summary_epoch(claim.session_id) == (1, 0)
        assert store.connection is not None
        row = await (
            await store.connection.execute(
                "SELECT status,reason_code,exception_type FROM summary_jobs"
            )
        ).fetchone()
        assert row is not None and tuple(row) == (
            "unknown",
            "ledger_unresolved",
            "unknown",
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_quarantine_lookup_failure_keeps_unknown_and_safe_type(tmp_path) -> None:
    """隔离查询失败时只记录异常类型，异常正文不能进入任务行。"""
    store, _, _, claim, _, _ = await _prepare_claim(tmp_path)
    store.set_summary_canonical_owner_lookup(AsyncMock(return_value=None))

    async def failed_lookup(_key: str):
        raise RuntimeError("quarantine database secret")

    store.set_summary_quarantine_candidate_lookup(failed_lookup)
    try:
        assert await store.reconcile_startup_candidates() == 1
        assert await store.get_summary_epoch(claim.session_id) == (1, 0)
        assert store.connection is not None
        row = await (
            await store.connection.execute(
                "SELECT status,exception_type FROM summary_jobs"
            )
        ).fetchone()
        assert row is not None and tuple(row) == ("unknown", "RuntimeError")
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        {"candidate_key": "other-key"},
        {"status": "approved"},
        {"source_digest": "changed"},
    ],
)
async def test_mismatched_quarantine_evidence_does_not_close_slot(
    tmp_path, evidence
) -> None:
    """key、状态或来源任一不一致都保守保留 UNKNOWN。"""
    store, _, _, claim, intent, _ = await _prepare_claim(tmp_path)
    base = {
        "candidate_key": intent.idempotency_key,
        "status": "pending",
        "session_id": claim.session_id,
        "start_seq": claim.start_seq,
        "end_seq": claim.end_seq,
        "expected_count": claim.expected_count,
        "session_epoch": claim.session_epoch,
        "source_digest": claim.source_digest,
    }
    base.update(evidence)
    store.set_summary_canonical_owner_lookup(AsyncMock(return_value=None))
    store.set_summary_quarantine_candidate_lookup(AsyncMock(return_value=base))
    try:
        assert await store.reconcile_startup_candidates() == 1
        assert await store.get_summary_epoch(claim.session_id) == (1, 0)
        assert store.connection is not None
        row = await (
            await store.connection.execute(
                "SELECT status,disposition FROM summary_job_candidates"
            )
        ).fetchone()
        assert row is not None and tuple(row) == ("unknown", None)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_quality_fallback_key_never_joins_even_with_matching_shape(
    tmp_path,
) -> None:
    """旧 quality: fallback key 即使形状匹配也不能作为同幂等证据。"""
    store, _, _, claim, intent, _ = await _prepare_claim(
        tmp_path, key="quality:fallback"
    )
    evidence = {
        "candidate_key": intent.idempotency_key,
        "status": "pending",
        "session_id": claim.session_id,
        "start_seq": claim.start_seq,
        "end_seq": claim.end_seq,
        "expected_count": claim.expected_count,
        "session_epoch": claim.session_epoch,
        "source_digest": claim.source_digest,
    }
    store.set_summary_canonical_owner_lookup(AsyncMock(return_value=None))
    store.set_summary_quarantine_candidate_lookup(AsyncMock(return_value=evidence))
    try:
        assert await store.reconcile_startup_candidates() == 1
        assert await store.get_summary_epoch(claim.session_id) == (1, 0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_quarantine_lookup_cancellation_propagates(tmp_path) -> None:
    """隔离对账查询取消必须穿透，不得被降级为 UNKNOWN。"""
    store, _, _, _, _, _ = await _prepare_claim(tmp_path)
    store.set_summary_canonical_owner_lookup(AsyncMock(return_value=None))

    async def cancelled_lookup(_key: str):
        raise asyncio.CancelledError

    store.set_summary_quarantine_candidate_lookup(cancelled_lookup)
    try:
        with pytest.raises(asyncio.CancelledError):
            await store.reconcile_startup_candidates()
    finally:
        await store.close()
