"""总结窗口混合失败收口：canonical 证据不得被失败重试丢弃。"""

from __future__ import annotations

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
    SummaryReasonCode,
    SummaryWindowContext,
    WindowOutcome,
)
from core.shared.contracts.conversation import Message


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


async def _context(session_id: str, epoch: int, cursor: int) -> SummaryWindowContext:
    """构造启动扫描使用的私聊固定上下文。"""
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


@pytest.mark.asyncio
async def test_mixed_batch_failure_persists_canonical_ledger(tmp_db_path: str) -> None:
    """部分候选写入失败时，成功 canonical 的 owner 必须落 ledger 并阻断 abandon。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("mixed", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("mixed", 1, 0), 2)
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        assert await store.begin_candidate_intents(
            claims[0],
            (
                CandidateIntent(slot=0, content_digest="d1", idempotency_key="k1"),
                CandidateIntent(slot=1, content_digest="d2", idempotency_key="k2"),
            ),
        )
        outcome = WindowOutcome(
            can_advance=False,
            canonical_count=1,
            failed_count=1,
            candidate_slots=(
                CandidateIntent(
                    slot=0,
                    content_digest="d1",
                    idempotency_key="k1",
                    disposition=CandidateDisposition.CANONICAL,
                    status=CandidateLedgerStatus.COMMITTED,
                    canonical_id=42,
                ),
                CandidateIntent(
                    slot=1,
                    content_digest="d2",
                    idempotency_key="k2",
                    disposition=CandidateDisposition.FAILED,
                    status=CandidateLedgerStatus.FAILED,
                ),
            ),
            failed_stage="candidate_write",
            reason_code=SummaryReasonCode.UNKNOWN,
        )
        committed = await store.commit_window(claims[0], outcome)
        assert committed.accepted is True
        assert committed.status.value == "failed"

        assert store.connection is not None
        ledger_cursor = await store.connection.execute(
            "SELECT slot,disposition,canonical_id FROM summary_job_candidates WHERE job_id=?",
            (claims[0].job_id,),
        )
        rows = {
            int(row[0]): (str(row[1]), row[2]) for row in await ledger_cursor.fetchall()
        }
        assert rows == {0: ("canonical", 42), 1: ("failed", None)}

        job_cursor = await store.connection.execute(
            "SELECT status,reason_code,next_attempt_at FROM summary_jobs WHERE job_id=?",
            (claims[0].job_id,),
        )
        job_row = await job_cursor.fetchone()
        assert job_row is not None
        assert (str(job_row[0]), str(job_row[1])) == ("failed", "retry_scheduled")
        assert float(job_row[2]) > 100.0

        # 即便人工推进到 blocked，canonical 证据仍阻止 abandon。
        await store.connection.execute(
            "UPDATE summary_jobs SET status='blocked',reason_code='retry_exhausted' WHERE job_id=?",
            (claims[0].job_id,),
        )
        await store.connection.commit()
        assert await store.confirm_abandon_session_jobs("mixed", 1) == 0
    finally:
        await store.close()
