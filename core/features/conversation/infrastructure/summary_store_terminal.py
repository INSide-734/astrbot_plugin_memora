"""总结任务 Store 的终态、恢复和安全修剪操作。"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...reflection.domain.summary_models import (
    CandidateIntent,
    CandidateLedgerStatus,
    ClaimedJob,
    CompletionResult,
    EpochResult,
    RetryResult,
    SummaryFailure,
    SummaryJobStatus,
    SummaryReasonCode,
    SummaryTaskSnapshot,
    TrimResult,
    WindowOutcome,
    retry_delay_seconds,
)
from .summary_store_keys import owned_slot_key, source_guarded

_REASON_VALUES = {item.value for item in SummaryReasonCode}
_COUNTERS = (
    "canonical_total",
    "quarantine_total",
    "discard_total",
    "mark_write_total",
    "failed_candidate_total",
    "skipped_idempotent_total",
)


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _timestamp(value: datetime | float | None = None) -> float:
    """把可控时间值转换为非负 Unix 秒。"""
    if value is None:
        return max(0.0, time.time())
    return max(0.0, value.timestamp() if isinstance(value, datetime) else float(value))


def _valid_outcome(outcome: WindowOutcome) -> bool:
    """确保收口计数、候选动作和 ledger 状态彼此一致。"""
    if not isinstance(outcome, WindowOutcome):
        return False
    expected = {
        "canonical": outcome.canonical_count,
        "quarantined": outcome.quarantine_count,
        "discard": outcome.discard_count,
        "mark_write": outcome.mark_write_count,
        "failed": outcome.failed_count,
        "skipped_idempotent": outcome.skipped_idempotent_count,
    }
    actual = {key: 0 for key in expected}
    unknown = 0
    for intent in outcome.candidate_slots:
        if not isinstance(intent, CandidateIntent):
            return False
        if intent.status is CandidateLedgerStatus.UNKNOWN:
            if intent.disposition is not None:
                return False
            unknown += 1
            continue
        if intent.status is CandidateLedgerStatus.FAILED:
            if intent.disposition is not None and intent.disposition.value != "failed":
                return False
        elif intent.status is not CandidateLedgerStatus.COMMITTED:
            return False
        if intent.disposition is None or intent.disposition.value not in actual:
            return False
        actual[intent.disposition.value] += 1
    if unknown != outcome.unknown_count:
        return False
    if any(actual[key] != value for key, value in expected.items()):
        return False
    return not outcome.can_advance or (unknown == 0 and outcome.failed_count == 0)


class SummaryStoreTerminalMixin:
    """提供总结任务的 CAS 终态、恢复和 trim 保护。"""

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...

        async def _begin_summary(self) -> None: ...
        async def _rollback_summary(self) -> None: ...
        async def _claim_matches(self, claim: ClaimedJob) -> bool: ...
        async def _ensure_epoch(
            self, session_id: str, now: float
        ) -> tuple[int, int]: ...

    async def commit_window(
        self, claim: ClaimedJob, outcome: WindowOutcome
    ) -> CompletionResult:
        """收口当前窗口并推进连续 completed/abandoned 前缀。"""
        if self.connection is None:
            return CompletionResult(
                False,
                SummaryJobStatus.UNKNOWN,
                reason_code=SummaryReasonCode.STORE_UNAVAILABLE,
            )
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                if not await self._claim_matches(claim):
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.CLAIM_LOST,
                    )
                if not _valid_outcome(outcome):
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.INVALID_ACTION,
                    )
                if outcome.unknown_count:
                    status = SummaryJobStatus.UNKNOWN
                elif outcome.can_advance:
                    status = SummaryJobStatus.COMPLETED
                else:
                    status = SummaryJobStatus.FAILED
                for intent in outcome.candidate_slots:
                    if not isinstance(intent, CandidateIntent):
                        await self._rollback_summary()
                        return CompletionResult(
                            False,
                            SummaryJobStatus.UNKNOWN,
                            reason_code=SummaryReasonCode.INVALID_SLOT,
                        )
                    slot_key = await owned_slot_key(
                        self.connection,
                        claim.job_id,
                        intent.slot,
                        intent.content_digest,
                    )
                    updated = await self.connection.execute(
                        """
                        UPDATE summary_job_candidates SET disposition=?,status=?,canonical_id=?,updated_at=?
                        WHERE job_id=? AND slot=? AND slot_key=? AND content_digest=?
                        """,
                        (
                            intent.disposition.value if intent.disposition else None,
                            CandidateLedgerStatus.COMMITTED.value
                            if intent.status
                            in {
                                CandidateLedgerStatus.PLANNED,
                                CandidateLedgerStatus.WRITING,
                                CandidateLedgerStatus.COMMITTED,
                            }
                            else intent.status.value,
                            intent.canonical_id,
                            now,
                            claim.job_id,
                            intent.slot,
                            slot_key,
                            intent.content_digest,
                        ),
                    )
                    if updated.rowcount != 1:
                        await self._rollback_summary()
                        return CompletionResult(
                            False,
                            SummaryJobStatus.UNKNOWN,
                            reason_code=SummaryReasonCode.INVALID_SLOT,
                        )
                updated = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status=?,reason_code=?,failed_stage=?,lease_until=NULL,
                      claim_token=NULL,canonical_count=?,quarantine_count=?,discard_count=?,
                      mark_write_count=?,failed_count=?,skipped_count=?,updated_at=?
                    WHERE job_id=? AND session_id=? AND session_epoch=? AND status='running'
                      AND claim_token=? AND worker_generation=?
                    """,
                    (
                        status.value,
                        outcome.reason_code.value,
                        outcome.failed_stage,
                        outcome.canonical_count,
                        outcome.quarantine_count,
                        outcome.discard_count,
                        outcome.mark_write_count,
                        outcome.failed_count,
                        outcome.skipped_idempotent_count,
                        now,
                        claim.job_id,
                        claim.session_id,
                        claim.session_epoch,
                        claim.claim_token,
                        claim.worker_generation,
                    ),
                )
                if updated.rowcount != 1:
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.CLAIM_LOST,
                    )
                for counter, increment in {
                    "canonical_total": outcome.canonical_count,
                    "quarantine_total": outcome.quarantine_count,
                    "discard_total": outcome.discard_count,
                    "mark_write_total": outcome.mark_write_count,
                    "failed_candidate_total": outcome.failed_count,
                    "skipped_idempotent_total": outcome.skipped_idempotent_count,
                }.items():
                    if increment:
                        await self.connection.execute(
                            "UPDATE summary_task_counters SET value=value+? WHERE counter_name=?",
                            (increment, counter),
                        )
                cursor = await self._advance_cursor(
                    claim.session_id, claim.session_epoch, now
                )
                await self.connection.commit()
                return CompletionResult(True, status, cursor, outcome.reason_code)
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return CompletionResult(
                False, SummaryJobStatus.UNKNOWN, reason_code=SummaryReasonCode.UNKNOWN
            )

    async def _advance_cursor(self, session_id: str, epoch: int, now: float) -> int:
        """扫描连续终态窗口，并保留当前游标后的 pending projection。"""
        cursor = await self.connection.execute(
            "SELECT cursor_seq FROM session_epochs WHERE session_id=? AND epoch=?",
            (session_id, epoch),
        )
        row = await cursor.fetchone()
        current = int(_row(row, "cursor_seq", 0) or 0) if row else 0
        while True:
            cursor = await self.connection.execute(
                """
                SELECT end_seq FROM summary_jobs
                WHERE session_id=? AND session_epoch=? AND start_seq=?
                  AND status IN ('completed','abandoned')
                ORDER BY end_seq LIMIT 1
                """,
                (session_id, epoch, current),
            )
            row = await cursor.fetchone()
            if row is None:
                break
            end_seq = int(_row(row, "end_seq", 0))
            if end_seq <= current:
                break
            current = end_seq

        pending_cursor = await self.connection.execute(
            """
            SELECT start_seq,end_seq,status,reason_code
            FROM summary_jobs
            WHERE session_id=? AND session_epoch=? AND start_seq>=?
              AND status IN ('queued','running','failed','blocked','unknown','cancelled')
            ORDER BY start_seq,end_seq,created_at,job_id LIMIT 1
            """,
            (session_id, epoch, current),
        )
        pending_row = await pending_cursor.fetchone()
        pending_json = None
        if pending_row is not None:
            pending_json = json.dumps(
                {
                    "start_seq": int(_row(pending_row, "start_seq", 0)),
                    "end_seq": int(_row(pending_row, "end_seq", 1)),
                    "status": str(_row(pending_row, "status", 2)),
                    "reason_code": str(_row(pending_row, "reason_code", 3)),
                },
                separators=(",", ":"),
            )
        await self.connection.execute(
            """
            UPDATE session_epochs
            SET cursor_seq=?,pending_summary_json=?,updated_at=?
            WHERE session_id=? AND epoch=?
            """,
            (current, pending_json, now, session_id, epoch),
        )
        return current

    async def fail_window(
        self,
        claim: ClaimedJob,
        failure: SummaryFailure,
        *,
        now: datetime | float | None = None,
    ) -> RetryResult:
        """以 claim CAS 记录失败、退避或 blocked 终态。"""
        if self.connection is None:
            return RetryResult(
                False,
                SummaryJobStatus.UNKNOWN,
                reason_code=SummaryReasonCode.STORE_UNAVAILABLE,
            )
        now = _timestamp(now)
        try:
            async with self._write_lock:
                await self._begin_summary()
                if not await self._claim_matches(claim):
                    await self._rollback_summary()
                    return RetryResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.CLAIM_LOST,
                    )
                if failure.cancelled:
                    status, next_at, reason = (
                        SummaryJobStatus.CANCELLED,
                        now,
                        SummaryReasonCode.CANCELLED,
                    )
                elif not failure.retryable or claim.attempt_count >= 3:
                    status, next_at, reason = (
                        SummaryJobStatus.BLOCKED,
                        now,
                        SummaryReasonCode.RETRY_EXHAUSTED,
                    )
                else:
                    status = SummaryJobStatus.FAILED
                    next_at = now + retry_delay_seconds(claim.attempt_count)
                    reason = SummaryReasonCode.RETRY_SCHEDULED
                updated = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status=?,next_attempt_at=?,lease_until=NULL,
                      claim_token=NULL,failed_stage=?,reason_code=?,exception_type=?,
                      attempt_count=CASE WHEN ?=1 AND attempt_count>0
                                         THEN attempt_count-1 ELSE attempt_count END,
                      updated_at=?
                    WHERE job_id=? AND session_id=? AND session_epoch=? AND status='running'
                      AND claim_token=? AND worker_generation=?
                    """,
                    (
                        status.value,
                        next_at,
                        failure.failed_stage,
                        reason.value,
                        failure.exception_type,
                        int(bool(failure.cancelled)),
                        now,
                        claim.job_id,
                        claim.session_id,
                        claim.session_epoch,
                        claim.claim_token,
                        claim.worker_generation,
                    ),
                )
                if updated.rowcount != 1:
                    await self._rollback_summary()
                    return RetryResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.CLAIM_LOST,
                    )
                # 失败/取消可能与已完成的相邻窗口乱序到达，统一重算安全 projection。
                await self._advance_cursor(claim.session_id, claim.session_epoch, now)
                await self.connection.commit()
                return RetryResult(True, status, claim.attempt_count, next_at, reason)
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return RetryResult(
                False,
                SummaryJobStatus.UNKNOWN,
                reason_code=SummaryReasonCode.UNKNOWN,
            )

    async def requeue_claim(
        self,
        claim: ClaimedJob,
        reason_code: str,
        *,
        now: datetime | float | None = None,
    ) -> bool:
        """只把仍属于当前 token 的任务恢复为 queued，取消不消耗重试次数。"""
        if self.connection is None:
            return False
        reason = (
            reason_code
            if reason_code in _REASON_VALUES
            else SummaryReasonCode.UNKNOWN.value
        )
        stamp = _timestamp(now)
        try:
            async with self._write_lock:
                await self._begin_summary()
                cursor = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status='queued', next_attempt_at=?,
                      lease_until=NULL, claim_token=NULL,
                      attempt_count=CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END,
                      reason_code=?, updated_at=?
                    WHERE job_id=? AND session_id=? AND session_epoch=? AND status='running'
                      AND claim_token=? AND worker_generation=?
                    """,
                    (
                        stamp,
                        reason,
                        stamp,
                        claim.job_id,
                        claim.session_id,
                        claim.session_epoch,
                        claim.claim_token,
                        claim.worker_generation,
                    ),
                )
                if cursor.rowcount == 1:
                    await self._advance_cursor(
                        claim.session_id, claim.session_epoch, stamp
                    )
                await self.connection.commit()
                return cursor.rowcount == 1
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return False

    async def recover_expired_claims(self, now: datetime) -> int:
        """回收过期 lease，并清空旧 token。"""
        if self.connection is None:
            return 0
        stamp = _timestamp(now)
        try:
            async with self._write_lock:
                await self._begin_summary()
                cursor = await self.connection.execute(
                    "UPDATE summary_jobs SET status='queued',lease_until=NULL,claim_token=NULL,worker_generation=worker_generation+1,reason_code='lease_expired',updated_at=? WHERE status='running' AND lease_until IS NOT NULL AND lease_until<?",
                    (stamp, stamp),
                )
                await self.connection.commit()
                return max(0, int(cursor.rowcount))
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            raise RuntimeError("summary_recovery_failed") from error

    async def cancel_session_jobs(
        self, session_id: str, epoch: int, reason_code: str
    ) -> int:
        """按 epoch 取消指定 session 的非终态任务。"""
        if self.connection is None:
            return 0
        reason = (
            reason_code
            if reason_code in _REASON_VALUES
            else SummaryReasonCode.CANCELLED.value
        )
        try:
            async with self._write_lock:
                await self._begin_summary()
                cursor = await self.connection.execute(
                    "UPDATE summary_jobs SET status='cancelled',claim_token=NULL,lease_until=NULL,reason_code=?,updated_at=? WHERE session_id=? AND session_epoch=? AND status NOT IN ('completed','cancelled','abandoned')",
                    (reason, self._summary_now(), session_id, epoch),
                )
                await self.connection.commit()
                return max(0, int(cursor.rowcount))
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return 0

    async def reset_session_epoch(
        self, session_id: str, reason_code: str
    ) -> EpochResult:
        """递增 session epoch，取消旧任务并清空 cursor projection。"""
        if self.connection is None:
            return EpochResult(
                False, 1, reason_code=SummaryReasonCode.STORE_UNAVAILABLE
            )
        reason = (
            reason_code
            if reason_code in _REASON_VALUES
            else SummaryReasonCode.EPOCH_FENCED.value
        )
        try:
            async with self._write_lock:
                await self._begin_summary()
                now = self._summary_now()
                old, _ = await self._ensure_epoch(session_id, now)
                cancelled = await self.connection.execute(
                    "UPDATE summary_jobs SET status='cancelled',claim_token=NULL,lease_until=NULL,reason_code=?,updated_at=? WHERE session_id=? AND session_epoch=? AND status NOT IN ('completed','cancelled','abandoned')",
                    (reason, now, session_id, old),
                )
                new = old + 1
                await self.connection.execute(
                    "UPDATE session_epochs SET epoch=?,cursor_seq=0,pending_summary_json=NULL,tombstoned_at=?,updated_at=? WHERE session_id=? AND epoch=?",
                    (new, now, now, session_id, old),
                )
                await self.connection.commit()
                return EpochResult(
                    True,
                    new,
                    max(0, int(cancelled.rowcount)),
                    SummaryReasonCode.EPOCH_FENCED,
                )
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return EpochResult(
                False, 1, reason_code=SummaryReasonCode.STORE_UNAVAILABLE
            )

    async def has_trim_blocker(self, session_id: str, epoch: int) -> bool:
        """检查任务、candidate ledger 或隔离候选是否阻止 trim。"""
        if self.connection is None:
            return True
        quarantine_store = getattr(self, "quarantine_store", None)
        pending_check = getattr(quarantine_store, "has_pending_for_session", None)
        if callable(pending_check):
            try:
                pending = pending_check(session_id)
                if inspect.isawaitable(pending):
                    pending = await pending
                if pending:
                    return True
            except asyncio.CancelledError:
                raise
            except Exception:
                return True
        cursor = await self.connection.execute(
            """
            SELECT 1 FROM summary_jobs
            WHERE session_id=? AND session_epoch=?
              AND status IN ('queued','running','failed','blocked','unknown','cancelled')
            LIMIT 1
            """,
            (session_id, epoch),
        )
        if await cursor.fetchone() is not None:
            return True
        cursor = await self.connection.execute(
            """
            SELECT 1
            FROM summary_jobs
            WHERE session_id=? AND session_epoch=?
              AND status IN ('completed','abandoned')
              AND start_seq > (
                  SELECT cursor_seq FROM session_epochs
                  WHERE session_id=? AND epoch=?
              )
            LIMIT 1
            """,
            (session_id, epoch, session_id, epoch),
        )
        if await cursor.fetchone() is not None:
            return True
        cursor = await self.connection.execute(
            """
            SELECT 1 FROM summary_job_candidates c
            JOIN summary_jobs j ON j.job_id=c.job_id
            WHERE j.session_id=? AND j.session_epoch=?
              AND c.status IN ('planned','writing','unknown')
            LIMIT 1
            """,
            (session_id, epoch),
        )
        return await cursor.fetchone() is not None

    @source_guarded
    async def trim_if_safe(
        self, session_id: str, epoch: int, delete_count: int
    ) -> TrimResult:
        """在单一事务内检查 blocker、删除最旧消息并调整 cursor。"""
        if self.connection is None or delete_count <= 0:
            return TrimResult(False, reason_code=SummaryReasonCode.TRIM_BLOCKED)
        try:
            async with self._write_lock:
                await self._begin_summary()
                if await self.has_trim_blocker(session_id, epoch):
                    await self._rollback_summary()
                    return TrimResult(False, reason_code=SummaryReasonCode.TRIM_BLOCKED)
                cursor = await self.connection.execute(
                    "SELECT cursor_seq FROM session_epochs WHERE session_id=? AND epoch=?",
                    (session_id, epoch),
                )
                row = await cursor.fetchone()
                safe = max(0, int(delete_count))
                if safe <= 0:
                    await self._rollback_summary()
                    return TrimResult(False, reason_code=SummaryReasonCode.TRIM_BLOCKED)
                deleted = await self.connection.execute(
                    """
                    DELETE FROM messages WHERE id IN (
                        SELECT id FROM messages
                        WHERE session_id=? AND message_seq IS NOT NULL AND message_seq <= ?
                        ORDER BY message_seq ASC LIMIT ?
                    )
                    """,
                    (
                        session_id,
                        int(_row(row, "cursor_seq", 0) or 0) if row else 0,
                        safe,
                    ),
                )
                count = max(0, int(deleted.rowcount))
                await self.connection.execute(
                    "UPDATE sessions SET message_count=(SELECT COUNT(*) FROM messages WHERE session_id=?) WHERE session_id=?",
                    (session_id, session_id),
                )
                await self.connection.commit()
                return TrimResult(
                    bool(count),
                    count,
                    SummaryReasonCode.COMPLETED
                    if count
                    else SummaryReasonCode.TRIM_BLOCKED,
                )
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return TrimResult(False, reason_code=SummaryReasonCode.TRIM_BLOCKED)

    async def snapshot(self) -> SummaryTaskSnapshot:
        """返回仅包含 allowlist 标量的任务快照。"""
        if self.connection is None:
            return SummaryTaskSnapshot()
        cursor = await self.connection.execute(
            "SELECT status,COUNT(*) AS count FROM summary_jobs GROUP BY status",
            (),
        )
        counts = {
            str(_row(row, "status", 0)): int(_row(row, "count", 1) or 0)
            for row in await cursor.fetchall()
        }
        totals: dict[str, int] = {}
        for counter in _COUNTERS:
            value_cursor = await self.connection.execute(
                "SELECT value FROM summary_task_counters WHERE counter_name=?",
                (counter,),
            )
            row = await value_cursor.fetchone()
            totals[counter] = int(_row(row, "value", 0) or 0) if row else 0
        return SummaryTaskSnapshot(
            queued=counts.get("queued", 0),
            running=counts.get("running", 0),
            failed=counts.get("failed", 0),
            blocked=counts.get("blocked", 0),
            unknown=counts.get("unknown", 0),
            cancelled=counts.get("cancelled", 0),
            abandoned=counts.get("abandoned", 0),
            active_parallelism=counts.get("running", 0),
            target_parallelism=counts.get("queued", 0),
            canonical_total=totals["canonical_total"],
            quarantine_total=totals["quarantine_total"],
            discard_total=totals["discard_total"],
            mark_write_total=totals["mark_write_total"],
            failed_candidate_total=totals["failed_candidate_total"],
            skipped_idempotent_total=totals["skipped_idempotent_total"],
        )


__all__ = ["SummaryStoreTerminalMixin"]
