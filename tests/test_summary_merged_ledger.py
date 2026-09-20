"""merged 处置、重复 owner 规则与启动恢复的 ledger 契约。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.domain.summary_models import (
    CandidateDisposition,
    CandidateIntent,
    CandidateLedgerStatus,
    SummaryWindowContext,
    WindowOutcome,
)
from core.shared.contracts.conversation import Message

_SESSION = "merged-session"
_OWNER = 77


def _message(session_id: str, index: int) -> Message:
    """构造确定性测试消息。"""

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


async def _context(session_id: str) -> SummaryWindowContext:
    """构造固定两消息窗口的私聊上下文。"""

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


def _intents(*dispositions: CandidateDisposition | None) -> tuple[CandidateIntent, ...]:
    """按处置构造同窗口的 slot intent（None 表示仍在写入中）。"""

    return tuple(
        CandidateIntent(
            slot=index,
            content_digest=f"digest-{index}",
            idempotency_key=f"key-{index}",
            disposition=disposition,
            status=(
                CandidateLedgerStatus.COMMITTED
                if disposition is not None
                else CandidateLedgerStatus.WRITING
            ),
            canonical_id=_OWNER if disposition is not None else None,
        )
        for index, disposition in enumerate(dispositions)
    )


async def _claimed_store(tmp_path: Any) -> tuple[ConversationStore, Any]:
    """准备已领取的两候选窗口。"""

    store = ConversationStore(str(tmp_path / "conversations.db"))
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    for index in range(2):
        await store.add_message(_message(_SESSION, index))
    assert (
        await store.plan_and_enqueue_windows(await _context(_SESSION), 2)
    ).queued == 1
    claims = await store.claim_ready(100.0, "scheduler", 1)
    assert len(claims) == 1
    claim = claims[0]
    assert await store.begin_candidate_intents(
        claim,
        tuple(
            CandidateIntent(
                slot=index,
                content_digest=f"digest-{index}",
                idempotency_key=f"key-{index}",
            )
            for index in range(2)
        ),
    )
    return store, claim


async def _book_ledger(
    store: ConversationStore, claim: Any, dispositions: tuple[str, str]
) -> None:
    """直接把 ledger 收口为已提交行，模拟提交崩溃后的持久状态。"""

    assert store.connection is not None
    for slot, disposition in enumerate(dispositions):
        await store.connection.execute(
            "UPDATE summary_job_candidates SET status='committed',disposition=?,"
            "canonical_id=? WHERE job_id=? AND slot=?",
            (disposition, _OWNER, claim.job_id, slot),
        )
    await store.connection.commit()


@pytest.mark.asyncio
async def test_two_merged_slots_commit_with_shared_owner(tmp_path) -> None:
    """同窗口两个 merged 槽位共享 owner 时提交成功并推进 cursor。"""

    store, claim = await _claimed_store(tmp_path)
    try:
        outcome = WindowOutcome(
            can_advance=True,
            merged_count=2,
            facts_rejected_count=3,
            candidate_slots=_intents(
                CandidateDisposition.MERGED, CandidateDisposition.MERGED
            ),
        )
        committed = await store.commit_window(claim, outcome)

        assert committed.accepted is True
        assert committed.status.value == "completed"
        assert committed.cursor == claim.end_seq
        assert store.connection is not None
        job = await (
            await store.connection.execute(
                "SELECT status,canonical_count,merged_count,facts_rejected_count "
                "FROM summary_jobs WHERE job_id=?",
                (claim.job_id,),
            )
        ).fetchone()
        assert job is not None and tuple(job) == ("completed", 0, 2, 3)
        counter = await (
            await store.connection.execute(
                "SELECT value FROM summary_task_counters WHERE counter_name='merged_total'"
            )
        ).fetchone()
        assert counter is not None and int(counter[0]) == 2
        rows = await (
            await store.connection.execute(
                "SELECT slot,disposition,status,canonical_id "
                "FROM summary_job_candidates WHERE job_id=? ORDER BY slot",
                (claim.job_id,),
            )
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (0, "merged", "committed", _OWNER),
            (1, "merged", "committed", _OWNER),
        ]
        assert (
            await store.has_trim_blocker(_SESSION, 1, include_quarantine=False) is False
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconcile_accepts_shared_merged_owner(tmp_path) -> None:
    """提交崩溃后按 running 窗口对账：两个 merged 槽位可共享 owner 并完成。"""

    store, claim = await _claimed_store(tmp_path)
    try:
        await _book_ledger(store, claim, ("merged", "merged"))
        reconciled = await store.reconcile_window(claim, {0: _OWNER, 1: _OWNER})

        assert reconciled.accepted is True
        assert reconciled.status.value == "completed"
        assert reconciled.cursor == claim.end_seq
        assert store.connection is not None
        job = await (
            await store.connection.execute(
                "SELECT status,merged_count FROM summary_jobs WHERE job_id=?",
                (claim.job_id,),
            )
        ).fetchone()
        assert job is not None and tuple(job) == ("completed", 2)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dispositions",
    [("canonical", "merged"), ("merged", "canonical"), ("canonical", "canonical")],
)
async def test_duplicate_owner_without_pure_merged_group_stays_unknown(
    tmp_path, dispositions: tuple[str, str]
) -> None:
    """普通 canonical 重复或 merged 与 canonical 混用同一 owner 都必须 unknown。"""

    store, claim = await _claimed_store(tmp_path)
    try:
        await _book_ledger(store, claim, dispositions)
        reconciled = await store.reconcile_window(claim, {0: _OWNER, 1: _OWNER})

        assert reconciled.status.value == "unknown"
        assert store.connection is not None
        job = await (
            await store.connection.execute(
                "SELECT status,reason_code FROM summary_jobs WHERE job_id=?",
                (claim.job_id,),
            )
        ).fetchone()
        assert job is not None and tuple(job) == ("unknown", "ledger_unresolved")
        cursor = await (
            await store.connection.execute(
                "SELECT cursor_seq FROM session_epochs WHERE session_id=?",
                (_SESSION,),
            )
        ).fetchone()
        assert cursor is not None and int(cursor[0]) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_startup_recovers_merged_slot_from_merged_key(tmp_path) -> None:
    """启动恢复按 merged 键证明 owner，并写入 merged 处置与计数。"""

    store, claim = await _claimed_store(tmp_path)
    intents = _intents(CandidateDisposition.MERGED, None)
    assert await store.begin_candidate_write(claim, intents[0])
    assert await store.begin_candidate_write(claim, intents[1])
    await store.close()

    reopened = ConversationStore(str(tmp_path / "conversations.db"))
    await reopened.initialize()
    reopened.set_summary_clock(lambda: 100.0)
    merged_lookup = AsyncMock(return_value=_OWNER)
    canonical_lookup = AsyncMock(return_value=None)
    reopened.set_summary_merged_owner_lookup(merged_lookup)
    reopened.set_summary_canonical_owner_lookup(canonical_lookup)
    try:
        assert await reopened.reconcile_startup_candidates() == 1
        assert merged_lookup.await_count == 2
        assert canonical_lookup.await_count == 0
        assert reopened.connection is not None
        rows = await (
            await reopened.connection.execute(
                "SELECT slot,disposition,status,canonical_id "
                "FROM summary_job_candidates WHERE job_id=? ORDER BY slot",
                (claim.job_id,),
            )
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (0, "merged", "committed", _OWNER),
            (1, "merged", "committed", _OWNER),
        ]
        job = await (
            await reopened.connection.execute(
                "SELECT status,merged_count FROM summary_jobs WHERE job_id=?",
                (claim.job_id,),
            )
        ).fetchone()
        assert job is not None and tuple(job) == ("completed", 2)
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_startup_without_merged_evidence_keeps_unknown(tmp_path) -> None:
    """merged/canonical 证据都缺失时不得猜测 owner 或推进 cursor。"""

    store, claim = await _claimed_store(tmp_path)
    intents = _intents(CandidateDisposition.MERGED, None)
    assert await store.begin_candidate_write(claim, intents[0])
    store.set_summary_merged_owner_lookup(AsyncMock(return_value=None))
    store.set_summary_canonical_owner_lookup(AsyncMock(return_value=None))
    try:
        assert await store.reconcile_startup_candidates() == 1
        assert store.connection is not None
        job = await (
            await store.connection.execute(
                "SELECT status,reason_code FROM summary_jobs WHERE job_id=?",
                (claim.job_id,),
            )
        ).fetchone()
        assert job is not None and tuple(job) == ("unknown", "ledger_unresolved")
    finally:
        await store.close()
