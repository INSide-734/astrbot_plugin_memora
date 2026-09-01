"""总结任务 Store 的终态、恢复和安全修剪操作。"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...reflection.domain.summary_models import (
    CandidateDisposition,
    CandidateIntent,
    CandidateLedgerStatus,
    ClaimedJob,
    CompletionResult,
    EpochResult,
    RetryResult,
    SummaryFailure,
    SummaryJobStatus,
    SummaryReasonCode,
    TrimResult,
    WindowOutcome,
    retry_delay_seconds,
)
from .summary_store_abandon import SummaryStoreAbandonMixin
from .summary_store_keys import owned_slot_key, source_epoch_guarded, source_guarded
from .summary_store_outcomes import valid_window_outcome
from .summary_store_reconcile import SummaryStoreReconcileMixin
from .summary_store_snapshot import SummaryStoreSnapshotMixin

_REASON_VALUES = {item.value for item in SummaryReasonCode}
_CANONICAL_ID_DISPOSITIONS = frozenset(
    {
        CandidateDisposition.CANONICAL,
        CandidateDisposition.MARK_WRITE,
        CandidateDisposition.SKIPPED_IDEMPOTENT,
    }
)


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _timestamp(value: datetime | float | None = None) -> float:
    """把显式传入的时刻转换为非负 Unix 秒；缺省由 Store 调用方提供。"""
    if value is None:
        raise ValueError("summary_clock_required")
    return max(0.0, value.timestamp() if isinstance(value, datetime) else float(value))


class SummaryStoreTerminalMixin(
    SummaryStoreAbandonMixin, SummaryStoreReconcileMixin, SummaryStoreSnapshotMixin
):
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
                if not valid_window_outcome(outcome):
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.INVALID_ACTION,
                    )
                if outcome.unknown_count or (
                    not outcome.can_advance and outcome.failed_count == 0
                ):
                    status = SummaryJobStatus.UNKNOWN
                elif outcome.can_advance:
                    status = SummaryJobStatus.COMPLETED
                else:
                    status = SummaryJobStatus.FAILED
                final_reason = outcome.reason_code.value
                next_at: float | None = None
                if status is SummaryJobStatus.FAILED:
                    if claim.attempt_count >= 3:
                        status = SummaryJobStatus.BLOCKED
                        final_reason = SummaryReasonCode.RETRY_EXHAUSTED.value
                    else:
                        final_reason = SummaryReasonCode.RETRY_SCHEDULED.value
                        next_at = now + retry_delay_seconds(claim.attempt_count)
                ledger_cursor = await self.connection.execute(
                    "SELECT slot,slot_key,content_digest,idempotency_key,status,canonical_id "
                    "FROM summary_job_candidates WHERE job_id=?",
                    (claim.job_id,),
                )
                ledger = {
                    int(_row(row, "slot", 0)): (
                        _row(row, "slot_key", 1),
                        _row(row, "content_digest", 2),
                        str(_row(row, "idempotency_key", 3) or ""),
                        str(_row(row, "status", 4)),
                        _row(row, "canonical_id", 5),
                    )
                    for row in await ledger_cursor.fetchall()
                }
                intents: dict[int, tuple[str, CandidateIntent]] = {}
                for intent in outcome.candidate_slots:
                    if (
                        not isinstance(intent, CandidateIntent)
                        or intent.slot in intents
                    ):
                        return await self._mark_unknown_claim(claim, now)
                    slot_key = await owned_slot_key(
                        self.connection,
                        claim.job_id,
                        intent.slot,
                        intent.content_digest,
                    )
                    intents[intent.slot] = (slot_key, intent)
                if set(ledger) != set(intents):
                    return await self._mark_unknown_claim(claim, now)
                for slot, (slot_key, intent) in intents.items():
                    existing = ledger[slot]
                    requires_canonical_id = (
                        intent.disposition in _CANONICAL_ID_DISPOSITIONS
                    )
                    mapping_inconsistent = (
                        (requires_canonical_id and intent.canonical_id is None)
                        or (
                            not requires_canonical_id
                            and intent.canonical_id is not None
                        )
                        or (
                            existing[4] is not None
                            and existing[4] != intent.canonical_id
                        )
                    )
                    if (
                        existing[0] != slot_key
                        or existing[1] != intent.content_digest
                        or existing[2] != intent.idempotency_key
                        or (
                            existing[3] == CandidateLedgerStatus.UNKNOWN.value
                            and intent.status is not CandidateLedgerStatus.UNKNOWN
                        )
                        or mapping_inconsistent
                    ):
                        return await self._mark_unknown_claim(claim, now)
                for slot, (slot_key, intent) in intents.items():
                    updated = await self.connection.execute(
                        """
                        UPDATE summary_job_candidates
                        SET disposition=?,status=?,canonical_id=COALESCE(?,canonical_id),updated_at=?
                        WHERE job_id=? AND slot=? AND slot_key=? AND content_digest=?
                          AND idempotency_key=?
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
                            slot,
                            slot_key,
                            intent.content_digest,
                            intent.idempotency_key,
                        ),
                    )
                    if updated.rowcount != 1:
                        return await self._mark_unknown_claim(claim, now)
                updated = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status=?,reason_code=?,next_attempt_at=COALESCE(?,next_attempt_at),
                      failed_stage=?,lease_until=NULL,claim_token=NULL,canonical_count=?,quarantine_count=?,
                      discard_count=?,mark_write_count=?,failed_count=?,skipped_count=?,updated_at=?
                    WHERE job_id=? AND session_id=? AND session_epoch=? AND status='running'
                      AND claim_token=? AND worker_generation=?
                    """,
                    (
                        status.value,
                        final_reason,
                        next_at,
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
                if status is SummaryJobStatus.COMPLETED:
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
                return CompletionResult(
                    True, status, cursor, SummaryReasonCode(final_reason)
                )
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
        # 未显式传入时间时必须读取 Store 注入时钟，不能退回进程 wall clock。
        stamp = self._summary_now() if now is None else _timestamp(now)
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
                        stamp,
                        SummaryReasonCode.CANCELLED,
                    )
                elif not failure.retryable or claim.attempt_count >= 3:
                    status, next_at, reason = (
                        SummaryJobStatus.BLOCKED,
                        stamp,
                        SummaryReasonCode.RETRY_EXHAUSTED,
                    )
                else:
                    status = SummaryJobStatus.FAILED
                    next_at = stamp + retry_delay_seconds(claim.attempt_count)
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
                        stamp,
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
                await self._advance_cursor(claim.session_id, claim.session_epoch, stamp)
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

    async def renew_claim(
        self,
        claim: ClaimedJob,
        lease_seconds: int,
        *,
        now: datetime | float | None = None,
    ) -> bool:
        """仅为仍持有当前 token 的 running claim 延长租约。"""

        if self.connection is None or lease_seconds <= 0:
            return False
        stamp = self._summary_now() if now is None else _timestamp(now)
        try:
            async with self._write_lock:
                await self._begin_summary()
                updated = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET lease_until=?,updated_at=?
                    WHERE job_id=? AND session_id=? AND session_epoch=?
                      AND status='running' AND claim_token=?
                      AND worker_generation=? AND lease_until IS NOT NULL
                      AND lease_until>?
                    """,
                    (
                        stamp + max(1, int(lease_seconds)),
                        stamp,
                        claim.job_id,
                        claim.session_id,
                        claim.session_epoch,
                        claim.claim_token,
                        claim.worker_generation,
                        stamp,
                    ),
                )
                await self.connection.commit()
                return updated.rowcount == 1
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return False

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
        stamp = self._summary_now() if now is None else _timestamp(now)
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
        """回收过期 lease，并同步受影响会话的 cursor/pending projection。"""
        if self.connection is None:
            return 0
        stamp = _timestamp(now)
        try:
            async with self._write_lock:
                await self._begin_summary()
                affected_cursor = await self.connection.execute(
                    """
                    SELECT DISTINCT session_id, session_epoch
                    FROM summary_jobs
                    WHERE status='running' AND lease_until IS NOT NULL AND lease_until<=?
                    """,
                    (stamp,),
                )
                affected = [
                    (
                        str(_row(row, "session_id", 0)),
                        int(_row(row, "session_epoch", 1)),
                    )
                    for row in await affected_cursor.fetchall()
                ]
                cursor = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET
                      status=CASE WHEN attempt_count >= 3 THEN 'blocked' ELSE 'queued' END,
                      lease_until=NULL,claim_token=NULL,worker_generation=worker_generation+1,
                      reason_code=CASE WHEN attempt_count >= 3 THEN 'retry_exhausted' ELSE 'lease_expired' END,
                      failed_stage=CASE WHEN attempt_count >= 3 THEN 'lease_recovery' ELSE failed_stage END,
                      updated_at=?
                    WHERE status='running' AND lease_until IS NOT NULL AND lease_until<=?
                    """,
                    (stamp, stamp),
                )
                for session_id, epoch in affected:
                    await self._advance_cursor(session_id, epoch, stamp)
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

    @source_epoch_guarded
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

    async def _has_quarantine_trim_blocker(self, session_id: str) -> bool:
        """在 Store 事务外检查隔离候选是否仍依赖原始来源。"""
        quarantine_store = getattr(self, "quarantine_store", None)
        pending_check = getattr(quarantine_store, "has_pending_for_session", None)
        if not callable(pending_check):
            return False
        try:
            pending = pending_check(session_id)
            if inspect.isawaitable(pending):
                pending = await pending
            return bool(pending)
        except asyncio.CancelledError:
            raise
        except Exception:
            return True

    async def has_trim_blocker(
        self, session_id: str, epoch: int, *, include_quarantine: bool = True
    ) -> bool:
        """检查任务、candidate ledger 或隔离候选是否阻止 trim。

        Store 事务内调用时关闭隔离检查；外部调用保留完整的跨库检查。
        """
        if self.connection is None:
            return True
        if include_quarantine and await self._has_quarantine_trim_blocker(session_id):
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
            SELECT 1
            FROM summary_jobs j
            JOIN summary_job_candidates c ON c.job_id=j.job_id
            WHERE j.session_id=? AND j.session_epoch=?
              AND j.status NOT IN ('completed','abandoned')
              AND c.status IN ('planned','writing','failed','unknown')
            LIMIT 1
            """,
            (session_id, epoch),
        )
        if await cursor.fetchone() is not None:
            return True
        # completed/abandoned 任务也必须保留完整、互相一致的 ledger 证据；
        # 只检查开放状态会让损坏的终态来源被误删。
        cursor = await self.connection.execute(
            """
            SELECT 1
            FROM summary_jobs AS j
            JOIN summary_job_candidates AS c ON c.job_id=j.job_id
            WHERE j.session_id=? AND j.session_epoch=?
              AND j.status IN ('completed','abandoned')
              AND (
                c.status NOT IN ('committed','failed')
                OR c.disposition IS NULL
                OR (c.disposition IN ('canonical','mark_write','skipped_idempotent')
                    AND (c.status <> 'committed' OR c.canonical_id IS NULL))
                OR (c.disposition IN ('quarantined','discard')
                    AND (c.status <> 'committed' OR c.canonical_id IS NOT NULL))
                OR (c.disposition='failed' AND c.status <> 'failed')
              )
            LIMIT 1
            """,
            (session_id, epoch),
        )
        if await cursor.fetchone() is not None:
            return True
        # 有候选结果的终态任务必须保留完整 ledger；空候选窗口无需伪造 slot。
        cursor = await self.connection.execute(
            """
            SELECT 1
            FROM summary_jobs AS j
            WHERE j.session_id=? AND j.session_epoch=?
              AND j.status IN ('completed','abandoned')
              AND (
                  j.canonical_count + j.quarantine_count + j.discard_count
                  + j.mark_write_count + j.failed_count + j.skipped_count
              ) > 0
              AND NOT EXISTS (
                SELECT 1 FROM summary_job_candidates AS c WHERE c.job_id=j.job_id
              )
            LIMIT 1
            """,
            (session_id, epoch),
        )
        if await cursor.fetchone() is not None:
            return True
        # 终态任务的计数必须与 ledger 的每种处置逐项相等，避免漏 slot。
        cursor = await self.connection.execute(
            """
            SELECT 1
            FROM summary_jobs AS j
            LEFT JOIN summary_job_candidates AS c ON c.job_id=j.job_id
            WHERE j.session_id=? AND j.session_epoch=?
              AND j.status IN ('completed','abandoned')
            GROUP BY j.job_id
            HAVING COUNT(c.slot) != (
                       j.canonical_count + j.quarantine_count + j.discard_count
                       + j.mark_write_count + j.failed_count + j.skipped_count
                   )
                OR SUM(CASE WHEN c.disposition='canonical' THEN 1 ELSE 0 END)
                   != j.canonical_count
                OR SUM(CASE WHEN c.disposition='quarantined' THEN 1 ELSE 0 END)
                   != j.quarantine_count
                OR SUM(CASE WHEN c.disposition='discard' THEN 1 ELSE 0 END)
                   != j.discard_count
                OR SUM(CASE WHEN c.disposition='mark_write' THEN 1 ELSE 0 END)
                   != j.mark_write_count
                OR SUM(CASE WHEN c.disposition='failed' THEN 1 ELSE 0 END)
                   != j.failed_count
                OR SUM(CASE WHEN c.disposition='skipped_idempotent' THEN 1 ELSE 0 END)
                   != j.skipped_count
            LIMIT 1
            """,
            (session_id, epoch),
        )
        return await cursor.fetchone() is not None

    @source_epoch_guarded
    @source_guarded
    async def trim_if_safe(
        self, session_id: str, epoch: int, delete_count: int
    ) -> TrimResult:
        """在单一事务内检查 blocker、删除最旧消息并调整 cursor。"""
        if self.connection is None or delete_count <= 0:
            return TrimResult(False, reason_code=SummaryReasonCode.TRIM_BLOCKED)
        if await self._has_quarantine_trim_blocker(session_id):
            return TrimResult(False, reason_code=SummaryReasonCode.TRIM_BLOCKED)
        try:
            async with self._write_lock:
                await self._begin_summary()
                if await self.has_trim_blocker(
                    session_id, epoch, include_quarantine=False
                ):
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


__all__ = ["SummaryStoreTerminalMixin"]
