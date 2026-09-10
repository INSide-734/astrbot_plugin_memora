"""持久化总结任务的领取、游标、对账与安全修剪契约。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.application.summary_worker import SummaryWorker
from core.features.reflection.domain.storage_outcomes import (
    ReflectionStoreOutcome,
    ReflectionStoreResult,
)
from core.features.reflection.domain.summary_models import (
    CandidateDisposition,
    CandidateIntent,
    CandidateLedgerStatus,
    SummaryFailure,
    SummaryJobStatus,
    SummaryReasonCode,
    SummaryWindowContext,
    WindowOutcome,
)
from core.shared.contracts.conversation import Message


def _message(session_id: str, index: int) -> Message:
    """构造确定性的测试消息。"""
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
async def test_empty_candidate_window_remains_trim_safe(tmp_db_path: str) -> None:
    """没有候选的已完成窗口也应留下可验证的 trim 证据。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await store.add_message(_message("empty", index))
        context = await _context("empty", 1, 0)
        assert (await store.plan_and_enqueue_windows(context, 4)).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        assert await store.begin_candidate_intents(claims[0], ())
        committed = await store.commit_window(
            claims[0], WindowOutcome(can_advance=True)
        )

        assert committed.accepted is True
        assert committed.cursor == 4
        trim = await store.trim_if_safe("empty", 1, 2)
        assert trim.accepted is True
        assert trim.deleted_count == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_no_facts_commit_reconcile_preserves_completed_state(
    tmp_db_path: str,
) -> None:
    """无事实提交无 ledger 时，对账不得降级为 unknown。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await store.add_message(_message("no-facts-reconcile", index))
        context = await _context("no-facts-reconcile", 1, 0)
        assert (await store.plan_and_enqueue_windows(context, 4)).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        committed = await store.commit_window(
            claims[0],
            WindowOutcome(can_advance=True, reason_code=SummaryReasonCode.NO_FACTS),
        )
        assert committed.accepted is True

        reconciled = await store.reconcile_window(claims[0], {})

        assert reconciled.accepted is True
        assert reconciled.status is SummaryJobStatus.COMPLETED
        assert reconciled.reason_code is SummaryReasonCode.NO_FACTS
        assert await store.get_summary_epoch("no-facts-reconcile") == (1, 4)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_trimmed_session_keeps_message_seq_highwater(tmp_db_path: str) -> None:
    """删除全部已总结消息后，新消息继续使用 cursor 后的单调序号。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await store.add_message(_message("highwater", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("highwater", 1, 0), 4)
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        assert await store.begin_candidate_intents(claims[0], ())
        committed = await store.commit_window(
            claims[0], WindowOutcome(can_advance=True)
        )
        assert committed.cursor == 4
        assert (await store.trim_if_safe("highwater", 1, 4)).deleted_count == 4
        assert await store.get_message_seq_end("highwater") == 4

        await store.add_message(_message("highwater", 4))
        await store.add_message(_message("highwater", 5))
        assert store.connection is not None
        seq_cursor = await store.connection.execute(
            "SELECT message_seq FROM messages WHERE session_id=? ORDER BY message_seq",
            ("highwater",),
        )
        assert [int(row[0]) for row in await seq_cursor.fetchall()] == [5, 6]
        assert await store.get_message_seq_end("highwater") == 6
        result = await store.plan_and_enqueue_windows(
            await _context("highwater", 1, 4), 6
        )
        assert result.queued == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_malformed_claim_is_blocked_instead_of_remaining_queued(
    tmp_db_path: str,
) -> None:
    """不可恢复 GateSnapshot 的 queued 任务必须收口为 blocked。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("malformed", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("malformed", 1, 0), 2)
        ).queued == 1
        assert store.connection is not None
        await store.connection.execute(
            "UPDATE summary_jobs SET gate_snapshot_json='[]' WHERE session_id=?",
            ("malformed",),
        )
        await store.connection.commit()

        assert await store.claim_ready(100.0, "scheduler", 1) == []
        cursor = await store.connection.execute(
            "SELECT status,reason_code,failed_stage FROM summary_jobs WHERE session_id=?",
            ("malformed",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert tuple(row) == ("blocked", "blocked", "claim_validate")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expired_claim_after_third_attempt_becomes_blocked(
    tmp_db_path: str,
) -> None:
    """第三次 lease 过期后任务必须 dead-letter，不能第四次执行。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("retry-limit", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("retry-limit", 1, 0), 2)
        ).queued == 1
        for stamp in (100.0, 200.0, 300.0):
            claims = await store.claim_ready(
                stamp, f"scheduler-{int(stamp)}", 1, lease_seconds=1
            )
            assert len(claims) == 1
            assert (
                await store.recover_expired_claims(
                    datetime.fromtimestamp(stamp + 1, timezone.utc)
                )
                == 1
            )
        assert await store.claim_ready(400.0, "scheduler-fourth", 1) == []
        assert store.connection is not None
        cursor = await store.connection.execute(
            "SELECT status,reason_code,attempt_count FROM summary_jobs WHERE session_id=?",
            ("retry-limit",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert tuple(row) == ("blocked", "retry_exhausted", 3)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_summary_invalid_retries_then_blocks_with_source_preserved(
    tmp_db_path: str,
) -> None:
    """invalid 必须保留固定原因有界重试，终态前后都不得推进或 trim 来源。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("summary-invalid", index))
        assert (
            await store.plan_and_enqueue_windows(
                await _context("summary-invalid", 1, 0),
                2,
            )
        ).queued == 1

        for stamp in (100.0, 105.0, 115.0):
            claims = await store.claim_ready(stamp, f"scheduler-{stamp}", 1)
            assert len(claims) == 1
            failed = await store.fail_window(
                claims[0],
                SummaryFailure(
                    failed_stage="memory_extract",
                    reason_code=SummaryReasonCode.SUMMARY_INVALID,
                    exception_type="SummaryParseError",
                    retryable=True,
                ),
                now=stamp,
            )
            assert failed.reason_code is SummaryReasonCode.SUMMARY_INVALID

        assert failed.status is SummaryJobStatus.BLOCKED
        assert await store.get_summary_epoch("summary-invalid") == (1, 0)
        trim = await store.trim_if_safe("summary-invalid", 1, 2)
        assert trim.accepted is False
        assert await store.get_message_seq_end("summary-invalid") == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_renewed_claim_remains_active_with_original_claim_dto(
    tmp_db_path: str,
) -> None:
    """续租更新数据库租约后，原 claim token 仍应通过 fencing。"""
    now = 100.0
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: now)
    try:
        for index in range(2):
            await store.add_message(_message("lease-renew", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("lease-renew", 1, 0), 2)
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1, lease_seconds=2)
        assert len(claims) == 1
        claim = claims[0]

        now = 101.0
        assert await store.renew_claim(claim, 4, now=now) is True
        assert await store.claim_is_active(claim) is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_two_connections_only_one_claims_job(tmp_db_path: str) -> None:
    """两个独立连接并发领取同一窗口时只能产生一个有效 claim。"""
    first = ConversationStore(tmp_db_path)
    await first.initialize()
    first.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await first.add_message(_message("cas", index))
        assert (
            await first.plan_and_enqueue_windows(await _context("cas", 1, 0), 4)
        ).queued == 1

        second = ConversationStore(tmp_db_path)
        await second.initialize()
        second.set_summary_clock(lambda: 100.0)
        try:
            claimed = await asyncio.gather(
                first.claim_ready(100.0, "first", 1),
                second.claim_ready(100.0, "second", 1),
            )
            assert sorted(len(item) for item in claimed) == [0, 1]
        finally:
            await second.close()
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_admin_abandon_advances_only_explicitly_confirmed_job(
    tmp_db_path: str,
) -> None:
    """管理员确认后只能放弃无 canonical 证据的阻塞窗口。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await store.add_message(_message("abandon", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("abandon", 1, 0), 4)
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        failed = await store.fail_window(
            claims[0],
            SummaryFailure(
                failed_stage="source_read",
                reason_code=SummaryReasonCode.SOURCE_INCOMPLETE,
                retryable=False,
            ),
        )
        assert failed.status.value == "blocked"

        assert await store.confirm_abandon_session_jobs("abandon", 1) == 1
        assert await store.get_summary_epoch("abandon") == (1, 4)
        assert store.connection is not None
        cursor = await store.connection.execute(
            "SELECT status,operator_action,reason_code,canonical_count "
            "FROM summary_jobs WHERE session_id=?",
            ("abandon",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert tuple(row) == (
            "abandoned",
            "admin_confirmed",
            "abandoned_confirmed",
            0,
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cursor_advances_only_after_contiguous_completed_prefix(
    tmp_db_path: str,
) -> None:
    """后窗口先完成时游标不动，前窗口完成后一次推进完整前缀。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(8):
            await store.add_message(_message("prefix", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("prefix", 1, 0), 8)
        ).queued == 2
        claims = []
        for _ in range(2):
            batch = await store.claim_ready(
                100.0,
                "scheduler",
                1,
                max_parallel_per_session=2,
            )
            assert len(batch) == 1
            claims.append(batch[0])

        later = await store.commit_window(claims[1], WindowOutcome(can_advance=True))
        assert later.accepted is True
        assert later.cursor == 0
        earlier = await store.commit_window(claims[0], WindowOutcome(can_advance=True))
        assert earlier.accepted is True
        assert earlier.cursor == 8
    finally:
        await store.close()


class _DeterministicProcessor:
    """返回固定候选，模拟重启前后稳定抽取。"""

    async def process_conversation(self, **_context: object) -> list[dict[str, object]]:
        """返回一条固定 canonical 候选。"""
        return [
            {
                "content": "用户偏好咖啡",
                "importance": 0.8,
                "metadata": {},
                "atoms": [],
            }
        ]


class _SingleBatchPreparer:
    """保持固定窗口顺序并返回唯一基础批次。"""

    async def prepare_batches(
        self, messages: list[Message], _is_group_chat: bool
    ) -> list[list[Message]]:
        """返回包含全部消息的单批次。"""
        return [messages]


class _IdempotentMemoryEngine:
    """以内存映射模拟 canonical 幂等 owner。"""

    def __init__(self) -> None:
        """初始化 owner 映射和物理写次数。"""
        self.owners: dict[str, int] = {}
        self.write_count = 0

    async def find_memory_id_by_idempotency_key(self, key: str) -> int | None:
        """按稳定幂等键返回现有 owner。"""
        return self.owners.get(key)

    async def add_memory(self, **payload: object) -> int:
        """首次写入创建 owner，后续相同键返回既有 owner。"""
        metadata = payload.get("metadata")
        assert isinstance(metadata, dict)
        key = metadata.get("idempotency_key")
        assert isinstance(key, str) and key
        owner = self.owners.get(key)
        if owner is not None:
            return owner
        self.write_count += 1
        self.owners[key] = 41
        return 41


@pytest.mark.asyncio
async def test_canonical_write_before_job_commit_recovers_without_duplicate(
    tmp_db_path: str,
) -> None:
    """canonical 已写而 job 未提交时，lease 恢复不得再次物理写入。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    engine = _IdempotentMemoryEngine()
    worker = SummaryWorker(
        cast(Any, store),
        cast(Any, _DeterministicProcessor()),
        None,
        cast(Any, engine),
        cast(Any, _SingleBatchPreparer()),
    )
    try:
        for index in range(4):
            await store.add_message(_message("crash", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("crash", 1, 0), 4)
        ).queued == 1
        first_claims = await store.claim_ready(100.0, "first", 1, lease_seconds=1)
        assert len(first_claims) == 1

        first_outcome = await worker.execute(first_claims[0])
        assert first_outcome.canonical_count == 1
        assert engine.write_count == 1

        store.set_summary_clock(lambda: 200.0)
        assert (
            await store.recover_expired_claims(
                datetime.fromtimestamp(200.0, timezone.utc)
            )
            == 1
        )
        retry_claims = await store.claim_ready(200.0, "retry", 1)
        assert len(retry_claims) == 1
        retry_outcome = await worker.execute(retry_claims[0])
        committed = await store.commit_window(retry_claims[0], retry_outcome)

        assert retry_outcome.skipped_idempotent_count == 1
        assert engine.write_count == 1
        assert committed.accepted is True
        assert committed.cursor == 4
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_admin_abandon_reconciles_unknown_ledger_before_trim(
    tmp_db_path: str,
) -> None:
    """管理员确认后 unknown slot 应收口为 failed 并解除来源 trim 阻塞。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await store.add_message(_message("unknown-abandon", index))
        assert (
            await store.plan_and_enqueue_windows(
                await _context("unknown-abandon", 1, 0), 4
            )
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        intent = CandidateIntent(
            slot=0,
            content_digest="digest",
            slot_key="store-owned",
            status=CandidateLedgerStatus.UNKNOWN,
        )
        assert await store.begin_candidate_intents(claims[0], (intent,))
        committed = await store.commit_window(
            claims[0],
            WindowOutcome(
                can_advance=False,
                unknown_count=1,
                candidate_slots=(intent,),
                failed_stage="candidate_reconcile",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            ),
        )
        assert committed.status.value == "unknown"
        assert await store.confirm_abandon_session_jobs("unknown-abandon", 1) == 1
        assert await store.get_summary_epoch("unknown-abandon") == (1, 4)

        assert store.connection is not None
        ledger_cursor = await store.connection.execute(
            "SELECT status,disposition FROM summary_job_candidates"
        )
        ledger_row = await ledger_cursor.fetchone()
        assert ledger_row is not None
        assert tuple(ledger_row) == ("failed", "failed")
        trim = await store.trim_if_safe("unknown-abandon", 1, 2)
        assert trim.accepted is True
        assert trim.deleted_count == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_changed_candidate_idempotency_key_enters_unknown(
    tmp_db_path: str,
) -> None:
    """同 slot 重试若幂等键变化，必须停止收口而不是创建第二条 canonical。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("key-change", index))
        assert (
            await store.plan_and_enqueue_windows(await _context("key-change", 1, 0), 2)
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        original = CandidateIntent(
            slot=0,
            content_digest="digest",
            idempotency_key="key-a",
            slot_key="store-owned",
        )
        assert await store.begin_candidate_intents(claims[0], (original,))
        changed = CandidateIntent(
            slot=0,
            content_digest="digest",
            idempotency_key="key-b",
            slot_key="store-owned",
            disposition=CandidateDisposition.CANONICAL,
            status=CandidateLedgerStatus.COMMITTED,
            canonical_id=7,
        )
        result = await store.commit_window(
            claims[0],
            WindowOutcome(
                can_advance=True, canonical_count=1, candidate_slots=(changed,)
            ),
        )
        assert result.accepted is True
        assert result.status.value == "unknown"
        assert await store.get_summary_epoch("key-change") == (1, 0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_abandon_preserves_quarantine_and_counts_terminal_outcomes(
    tmp_db_path: str,
) -> None:
    """abandoned 收口不得丢失既有隔离计数，新增失败也要累计。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("abandon-counts", index))
        assert (
            await store.plan_and_enqueue_windows(
                await _context("abandon-counts", 1, 0), 2
            )
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        quarantined = CandidateIntent(
            slot=0,
            content_digest="digest-q",
            disposition=CandidateDisposition.QUARANTINED,
            status=CandidateLedgerStatus.COMMITTED,
        )
        unknown = CandidateIntent(
            slot=1,
            content_digest="digest-u",
            disposition=None,
            status=CandidateLedgerStatus.UNKNOWN,
        )
        assert await store.begin_candidate_intents(claims[0], (quarantined, unknown))
        result = await store.commit_window(
            claims[0],
            WindowOutcome(
                can_advance=False,
                quarantine_count=1,
                unknown_count=1,
                candidate_slots=(quarantined, unknown),
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            ),
        )
        assert result.status.value == "unknown"
        assert await store.confirm_abandon_session_jobs("abandon-counts", 1) == 1
        snapshot = await store.snapshot()
        assert snapshot.quarantine_total == 1
        assert snapshot.failed_candidate_total == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_round_robin_after_cursor_serves_unknown_session(
    tmp_db_path: str,
) -> None:
    """已知会话持续积压时，轮转 cursor 后仍应领取新会话窗口。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for session_id in ("a", "b"):
            for index in range(2):
                await store.add_message(_message(session_id, index))
            assert (
                await store.plan_and_enqueue_windows(
                    await _context(session_id, 1, 0), 2
                )
            ).queued == 1
        first = await store.claim_ready(
            100.0,
            "scheduler",
            1,
            session_order=("a",),
        )
        assert len(first) == 1
        assert first[0].session_id == "a"
        assert await store.requeue_claim(first[0], "cancelled", now=100.0)

        second = await store.claim_ready(
            100.0,
            "scheduler",
            1,
            session_order=("a",),
            round_robin_after="a",
        )
        assert len(second) == 1
        assert second[0].session_id == "b"
    finally:
        await store.close()


def test_candidate_digest_mismatch_enters_unknown_outcome() -> None:
    """候选摘要与预期摘要不一致时不得推进总结窗口。"""
    worker = object.__new__(SummaryWorker)
    intent = CandidateIntent(
        slot=0,
        content_digest="actual-digest",
        idempotency_key="candidate-key",
    )

    outcome = worker._build_outcome(
        (intent,),
        (
            ReflectionStoreResult(
                ReflectionStoreOutcome.CANONICAL,
                "candidate-key",
                7,
            ),
        ),
        expected_idempotency_keys=(("candidate-key", "expected-digest"),),
    )

    assert outcome.can_advance is False
    assert outcome.unknown_count == 1
    assert outcome.reason_code is SummaryReasonCode.LEDGER_UNRESOLVED
    assert outcome.candidate_slots[0].status is CandidateLedgerStatus.UNKNOWN


@pytest.mark.asyncio
async def test_candidate_side_effect_is_preceded_by_durable_writing_ledger(
    tmp_db_path: str,
) -> None:
    """canonical 副作用开始前，候选 slot 必须已持久化为 writing。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("writing-ledger", index))
        assert (
            await store.plan_and_enqueue_windows(
                await _context("writing-ledger", 1, 0), 2
            )
        ).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        intent = CandidateIntent(
            slot=0,
            content_digest="digest",
            idempotency_key="candidate-key",
        )
        assert await store.begin_candidate_intents(claims[0], (intent,))
        assert await store.begin_candidate_write(claims[0], intent) is True

        assert store.connection is not None
        cursor = await store.connection.execute(
            "SELECT status,disposition,canonical_id FROM summary_job_candidates "
            "WHERE job_id=? AND slot=0",
            (claims[0].job_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert tuple(row) == ("writing", None, None)
    finally:
        await store.close()
