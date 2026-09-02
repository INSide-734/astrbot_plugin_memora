"""总结候选跨库副作用的保守 reconcile 与 epoch fence。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from ...reflection.domain.summary_models import (
    CandidateDisposition,
    CandidateLedgerStatus,
    ClaimedJob,
    CompletionResult,
    SummaryJobStatus,
    SummaryReasonCode,
)
from .summary_store_keys import owned_slot_key, source_epoch_guarded, source_guarded

_CANONICAL_DISPOSITIONS = frozenset(
    {
        CandidateDisposition.CANONICAL.value,
        CandidateDisposition.MARK_WRITE.value,
        CandidateDisposition.SKIPPED_IDEMPOTENT.value,
    }
)
_TERMINAL_DISPOSITIONS = _CANONICAL_DISPOSITIONS | frozenset(
    {
        CandidateDisposition.QUARANTINED.value,
        CandidateDisposition.DISCARD.value,
        CandidateDisposition.FAILED.value,
    }
)


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _terminal_ledger_matches_job(job: Any, rows: Sequence[Any]) -> bool:
    """核对终态任务计数与候选 ledger 的逐类数量。"""
    fields = {
        CandidateDisposition.CANONICAL.value: 2,
        CandidateDisposition.QUARANTINED.value: 3,
        CandidateDisposition.DISCARD.value: 4,
        CandidateDisposition.MARK_WRITE.value: 5,
        CandidateDisposition.FAILED.value: 6,
        CandidateDisposition.SKIPPED_IDEMPOTENT.value: 7,
    }
    expected: dict[str, int] = {}
    for disposition, index in fields.items():
        value = _row(job, disposition, index)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        expected[disposition] = value
    actual = dict.fromkeys(fields, 0)
    for row in rows:
        disposition = str(_row(row, "disposition", 3) or "")
        if disposition not in actual:
            return False
        actual[disposition] += 1
    return len(rows) == sum(expected.values()) and actual == expected


class SummaryStoreReconcileMixin:
    """提供不持有跨库事务的候选映射收口和会话来源 fence。"""

    def _summary_source_lock_for(self, session_id: str) -> asyncio.Lock:
        """返回会话级来源锁，串行化 reset/trim 与外部 canonical 写入。"""
        locks = getattr(self, "_summary_source_locks", None)
        if locks is None:
            locks = {}
            setattr(self, "_summary_source_locks", locks)
        lock = locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[session_id] = lock
        return lock

    @asynccontextmanager
    async def _summary_source_locks_for(
        self, session_ids: Sequence[str]
    ) -> AsyncGenerator[None, None]:
        """按固定顺序持有多个 session 来源锁，保护 TTL 批量删除。"""
        async with AsyncExitStack() as stack:
            for session_id in sorted({item for item in session_ids if item}):
                await stack.enter_async_context(
                    self._summary_source_lock_for(session_id)
                )
            yield

    @asynccontextmanager
    async def _summary_quarantine_guard(self) -> AsyncGenerator[None, None]:
        """在已持有会话来源锁后进入隔离 Store 协调锁。"""
        guard = getattr(getattr(self, "quarantine_store", None), "source_guard", None)
        if callable(guard):
            async with cast(Any, guard()):
                yield
            return
        yield

    async def run_claim_side_effect(
        self,
        claim: ClaimedJob,
        operation: Callable[[], Awaitable[object]],
    ) -> object:
        """在当前 claim 的 epoch 来源锁内运行外部副作用，不持有 SQLite 事务。"""
        connection = getattr(self, "connection", None)
        if (
            connection is None
            or not isinstance(claim, ClaimedJob)
            or not callable(operation)
        ):
            raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
        if getattr(connection, "in_transaction", False):
            raise RuntimeError("summary_store_transaction_active")
        async with self._summary_source_lock_for(claim.session_id):
            if not await self._claim_matches(claim):
                raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
            if getattr(connection, "in_transaction", False):
                raise RuntimeError("summary_store_transaction_active")
            result = operation()
            if inspect.isawaitable(result):
                result = await result
            if getattr(connection, "in_transaction", False):
                raise RuntimeError("summary_store_transaction_active")
            if not await self._claim_matches(claim):
                raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
            return result

    @source_epoch_guarded
    @source_guarded
    async def clear_session_atomically(self, session_id: str) -> int:
        """在一次 Store 事务中 fence epoch、取消任务并删除会话消息。"""
        if getattr(self, "connection", None) is None:
            raise RuntimeError("数据库连接未初始化")
        pending_check = getattr(
            getattr(self, "quarantine_store", None), "has_pending_for_session", None
        )
        if callable(pending_check):
            pending = pending_check(session_id)
            if inspect.isawaitable(pending):
                pending = await cast(Any, pending)
            if pending:
                raise RuntimeError("summary_source_protected")
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                epoch, _ = await self._ensure_epoch(session_id, now)
                await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status='cancelled',claim_token=NULL,
                      lease_until=NULL,reason_code='epoch_fenced',updated_at=?
                    WHERE session_id=? AND session_epoch=?
                      AND status NOT IN ('completed','cancelled','abandoned')
                    """,
                    (now, session_id, epoch),
                )
                deleted = await self.connection.execute(
                    "DELETE FROM messages WHERE session_id=?", (session_id,)
                )
                count = max(0, int(deleted.rowcount))
                await self.connection.execute(
                    "UPDATE sessions SET message_count=0,metadata='{}' WHERE session_id=?",
                    (session_id,),
                )
                await self.connection.execute(
                    """
                    UPDATE session_epochs SET epoch=?,cursor_seq=0,
                      pending_summary_json=NULL,tombstoned_at=?,updated_at=?
                    WHERE session_id=? AND epoch=?
                    """,
                    (epoch + 1, now, now, session_id, epoch),
                )
                await self.connection.commit()
                return count
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            raise

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...
        def _summary_source_lock_for(self, session_id: str) -> asyncio.Lock: ...

        async def _begin_summary(self) -> None: ...
        async def _rollback_summary(self) -> None: ...
        async def _claim_matches(self, claim: ClaimedJob) -> bool: ...
        async def _advance_cursor(
            self, session_id: str, epoch: int, now: float
        ) -> int: ...
        async def _ensure_epoch(
            self, session_id: str, now: float
        ) -> tuple[int, int]: ...

    async def _mark_unknown_claim(
        self,
        claim: ClaimedJob,
        now: float,
        reason_code: SummaryReasonCode = SummaryReasonCode.LEDGER_UNRESOLVED,
    ) -> CompletionResult:
        """用 claim CAS 将不确定 job 与候选固定为 unknown，不推进游标。"""
        if not await self._claim_matches(claim):
            await self._rollback_summary()
            return CompletionResult(
                False,
                SummaryJobStatus.UNKNOWN,
                reason_code=SummaryReasonCode.CLAIM_LOST,
            )
        await self.connection.execute(
            """
            UPDATE summary_job_candidates
            SET status='unknown', disposition=NULL, updated_at=?
            WHERE job_id=?
            """,
            (now, claim.job_id),
        )
        updated = await self.connection.execute(
            """
            UPDATE summary_jobs SET status='unknown', reason_code=?,
              failed_stage='candidate_reconcile', lease_until=NULL,
              claim_token=NULL, updated_at=?
            WHERE job_id=? AND session_id=? AND session_epoch=?
              AND status='running' AND claim_token=? AND worker_generation=?
            """,
            (
                reason_code.value,
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
        await self.connection.commit()
        return CompletionResult(True, SummaryJobStatus.UNKNOWN, 0, reason_code)

    async def _mark_unknown_completed(
        self, claim: ClaimedJob, now: float
    ) -> CompletionResult:
        """将已完成但无法核对的同 epoch job 降级为 unknown。"""
        await self.connection.execute(
            """
            UPDATE summary_job_candidates
            SET status='unknown', disposition=NULL, updated_at=?
            WHERE job_id=?
            """,
            (now, claim.job_id),
        )
        updated = await self.connection.execute(
            """
            UPDATE summary_jobs SET status='unknown', reason_code=?,
              failed_stage='candidate_reconcile', updated_at=?
            WHERE job_id=? AND session_id=? AND session_epoch=?
              AND status='completed' AND worker_generation=?
              AND EXISTS (
                SELECT 1 FROM session_epochs
                WHERE session_id=? AND epoch=?
              )
            """,
            (
                SummaryReasonCode.LEDGER_UNRESOLVED.value,
                now,
                claim.job_id,
                claim.session_id,
                claim.session_epoch,
                claim.worker_generation,
                claim.session_id,
                claim.session_epoch,
            ),
        )
        if updated.rowcount != 1:
            await self._rollback_summary()
            return CompletionResult(
                False,
                SummaryJobStatus.UNKNOWN,
                reason_code=SummaryReasonCode.CLAIM_LOST,
            )
        await self.connection.commit()
        return CompletionResult(
            True,
            SummaryJobStatus.UNKNOWN,
            0,
            SummaryReasonCode.LEDGER_UNRESOLVED,
        )

    async def _reconcile_mapping(
        self,
        claim: ClaimedJob,
        rows: list[Any],
        mapping: Mapping[object, object],
        *,
        require_terminal: bool,
    ) -> dict[int, int] | None:
        """校验 opaque slot 映射、HMAC slot key、digest 和已有 canonical ID。"""
        if not isinstance(mapping, Mapping):
            return None
        by_slot = {int(_row(row, "slot", 0)): row for row in rows}
        by_key = {str(_row(row, "slot_key", 1)): row for row in rows}
        normalized: dict[int, int] = {}
        used_ids: set[int] = set()
        for raw_slot, raw_id in mapping.items():
            if isinstance(raw_slot, bool):
                return None
            row = (
                by_slot.get(raw_slot)
                if isinstance(raw_slot, int)
                else by_key.get(raw_slot.strip())
                if isinstance(raw_slot, str) and raw_slot.strip()
                else None
            )
            if row is None or isinstance(raw_id, bool) or not isinstance(raw_id, int):
                return None
            slot = int(_row(row, "slot", 0))
            canonical_id = int(raw_id)
            if (
                slot in normalized
                or canonical_id <= 0
                or canonical_id > 2**63 - 1
                or canonical_id in used_ids
            ):
                return None
            normalized[slot] = canonical_id
            used_ids.add(canonical_id)

        resolved_ids: set[int] = set()
        for row in rows:
            slot = int(_row(row, "slot", 0))
            slot_key = str(_row(row, "slot_key", 1) or "")
            content_digest = str(_row(row, "content_digest", 2) or "")
            if not slot_key or not content_digest:
                return None
            if slot_key != await owned_slot_key(
                self.connection, claim.job_id, slot, content_digest
            ):
                return None
            status = str(_row(row, "status", 4) or "")
            disposition = _row(row, "disposition", 3)
            disposition = str(disposition) if disposition is not None else None
            existing_id = _row(row, "canonical_id", 5)
            supplied_id = normalized.get(slot)
            if status == CandidateLedgerStatus.UNKNOWN.value:
                return None
            if existing_id is not None:
                if isinstance(existing_id, bool) or not isinstance(existing_id, int):
                    return None
                if int(existing_id) <= 0 or (
                    supplied_id is not None and int(existing_id) != supplied_id
                ):
                    return None
            resolved_id = int(existing_id) if existing_id is not None else supplied_id
            if resolved_id is not None:
                if resolved_id in resolved_ids:
                    return None
                resolved_ids.add(resolved_id)
            if disposition in _CANONICAL_DISPOSITIONS:
                if resolved_id is None:
                    return None
            elif disposition in {
                CandidateDisposition.QUARANTINED.value,
                CandidateDisposition.DISCARD.value,
                CandidateDisposition.FAILED.value,
            }:
                if resolved_id is not None:
                    return None
            elif disposition is None:
                if require_terminal or status not in {
                    CandidateLedgerStatus.PLANNED.value,
                    CandidateLedgerStatus.WRITING.value,
                    CandidateLedgerStatus.COMMITTED.value,
                }:
                    return None
                if (
                    resolved_id is None
                    and status == CandidateLedgerStatus.COMMITTED.value
                ):
                    return None
            else:
                return None
            if require_terminal and disposition == CandidateDisposition.FAILED.value:
                return None
        return normalized

    async def reconcile_window(
        self,
        claim: ClaimedJob,
        slot_to_canonical_id: Mapping[object, object],
    ) -> CompletionResult:
        """以 claim token/epoch/generation、slot key 和 digest CAS 收口映射。

        映射只允许不透明 slot（或其 HMAC key）到正整数 canonical ID。运行中
        可先收口已发现的 owner；当 ledger 已完整时，本操作还可恢复 job commit
        崩溃。任何不一致都进入 unknown，游标保持不变。
        """
        if self.connection is None or not isinstance(claim, ClaimedJob):
            return CompletionResult(
                False,
                SummaryJobStatus.UNKNOWN,
                reason_code=SummaryReasonCode.CLAIM_LOST,
            )
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                job_cursor = await self.connection.execute(
                    """
                    SELECT job.status,job.worker_generation,job.canonical_count,
                           job.quarantine_count,job.discard_count,job.mark_write_count,
                           job.failed_count,job.skipped_count,
                           job.start_seq,job.end_seq,job.expected_count,job.source_digest,
                           job.reason_code
                    FROM summary_jobs AS job
                    INNER JOIN session_epochs AS epoch
                      ON epoch.session_id=job.session_id
                     AND epoch.epoch=job.session_epoch
                    WHERE job.job_id=? AND job.session_id=? AND job.session_epoch=?
                    """,
                    (claim.job_id, claim.session_id, claim.session_epoch),
                )
                job = await job_cursor.fetchone()
                if job is None:
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.CLAIM_LOST,
                    )
                status = str(_row(job, "status", 0) or "")
                generation = int(_row(job, "worker_generation", 1) or 0)
                if generation != claim.worker_generation:
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.GENERATION_FENCED,
                    )
                source_matches = (
                    isinstance(_row(job, "start_seq", 8), int)
                    and not isinstance(_row(job, "start_seq", 8), bool)
                    and _row(job, "start_seq", 8) == claim.start_seq
                    and isinstance(_row(job, "end_seq", 9), int)
                    and not isinstance(_row(job, "end_seq", 9), bool)
                    and _row(job, "end_seq", 9) == claim.end_seq
                    and isinstance(_row(job, "expected_count", 10), int)
                    and not isinstance(_row(job, "expected_count", 10), bool)
                    and _row(job, "expected_count", 10) == claim.expected_count
                    and str(_row(job, "source_digest", 11) or "") == claim.source_digest
                )
                if not source_matches:
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.SOURCE_DIGEST_MISMATCH,
                    )
                ledger_cursor = await self.connection.execute(
                    """
                    SELECT slot,slot_key,content_digest,disposition,status,canonical_id
                    FROM summary_job_candidates WHERE job_id=? ORDER BY slot
                    """,
                    (claim.job_id,),
                )
                rows = list(await ledger_cursor.fetchall())
                if status == SummaryJobStatus.COMPLETED.value:
                    if not rows:
                        zero_counts = all(
                            int(_row(job, name, index) or 0) == 0
                            for name, index in (
                                ("canonical_count", 2),
                                ("quarantine_count", 3),
                                ("discard_count", 4),
                                ("mark_write_count", 5),
                                ("failed_count", 6),
                                ("skipped_count", 7),
                            )
                        )
                        reason_code = str(_row(job, "reason_code", 12) or "")
                        if (
                            zero_counts
                            and reason_code == SummaryReasonCode.NO_FACTS.value
                            and not slot_to_canonical_id
                        ):
                            cursor = await self.connection.execute(
                                "SELECT cursor_seq FROM session_epochs WHERE session_id=? AND epoch=?",
                                (claim.session_id, claim.session_epoch),
                            )
                            cursor_row = await cursor.fetchone()
                            await self.connection.commit()
                            return CompletionResult(
                                True,
                                SummaryJobStatus.COMPLETED,
                                int(_row(cursor_row, "cursor_seq", 0) or 0)
                                if cursor_row
                                else 0,
                                SummaryReasonCode.NO_FACTS,
                            )
                        return await self._mark_unknown_completed(claim, now)
                    if not _terminal_ledger_matches_job(job, rows):
                        return await self._mark_unknown_completed(claim, now)
                    normalized = await self._reconcile_mapping(
                        claim, rows, slot_to_canonical_id, require_terminal=True
                    )
                    if normalized is None:
                        return await self._mark_unknown_completed(claim, now)
                    rows_by_slot = {int(_row(row, "slot", 0)): row for row in rows}
                    for slot, canonical_id in normalized.items():
                        row = rows_by_slot[slot]
                        updated = await self.connection.execute(
                            """
                            UPDATE summary_job_candidates
                            SET canonical_id=?, updated_at=?
                            WHERE job_id=? AND slot=? AND slot_key=? AND content_digest=?
                              AND status='committed'
                              AND disposition IN ('canonical','mark_write','skipped_idempotent')
                              AND (canonical_id IS NULL OR canonical_id=?)
                              AND EXISTS (
                                SELECT 1 FROM summary_jobs
                                WHERE job_id=? AND session_id=? AND session_epoch=?
                                  AND status='completed' AND worker_generation=?
                              )
                            """,
                            (
                                canonical_id,
                                now,
                                claim.job_id,
                                slot,
                                _row(row, "slot_key", 1),
                                _row(row, "content_digest", 2),
                                canonical_id,
                                claim.job_id,
                                claim.session_id,
                                claim.session_epoch,
                                claim.worker_generation,
                            ),
                        )
                        if updated.rowcount != 1:
                            return await self._mark_unknown_completed(claim, now)
                    await self.connection.commit()
                    cursor = await self.connection.execute(
                        "SELECT cursor_seq FROM session_epochs WHERE session_id=? AND epoch=?",
                        (claim.session_id, claim.session_epoch),
                    )
                    row = await cursor.fetchone()
                    return CompletionResult(
                        True,
                        SummaryJobStatus.COMPLETED,
                        int(_row(row, "cursor_seq", 0) or 0) if row else 0,
                        SummaryReasonCode.COMPLETED,
                    )
                if status == SummaryJobStatus.UNKNOWN.value:
                    await self._rollback_summary()
                    return CompletionResult(
                        True,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
                    )
                if (
                    status != SummaryJobStatus.RUNNING.value
                    or not await self._claim_matches(claim)
                ):
                    await self._rollback_summary()
                    return CompletionResult(
                        False,
                        SummaryJobStatus.UNKNOWN,
                        reason_code=SummaryReasonCode.CLAIM_LOST,
                    )
                normalized = await self._reconcile_mapping(
                    claim, rows, slot_to_canonical_id, require_terminal=False
                )
                if normalized is None:
                    return await self._mark_unknown_claim(claim, now)
                rows_by_slot = {int(_row(row, "slot", 0)): row for row in rows}
                for slot, canonical_id in normalized.items():
                    row = rows_by_slot[slot]
                    updated = await self.connection.execute(
                        """
                        UPDATE summary_job_candidates
                        SET canonical_id=?, updated_at=?
                        WHERE job_id=? AND slot=? AND slot_key=? AND content_digest=?
                          AND status IN ('planned','writing','committed')
                          AND (canonical_id IS NULL OR canonical_id=?)
                          AND EXISTS (
                            SELECT 1 FROM summary_jobs
                            WHERE job_id=? AND session_id=? AND session_epoch=?
                              AND status='running' AND claim_token=? AND worker_generation=?
                          )
                        """,
                        (
                            canonical_id,
                            now,
                            claim.job_id,
                            slot,
                            _row(row, "slot_key", 1),
                            _row(row, "content_digest", 2),
                            canonical_id,
                            claim.job_id,
                            claim.session_id,
                            claim.session_epoch,
                            claim.claim_token,
                            claim.worker_generation,
                        ),
                    )
                    if updated.rowcount != 1:
                        return await self._mark_unknown_claim(claim, now)
                final_cursor = await self.connection.execute(
                    "SELECT disposition,status,canonical_id FROM summary_job_candidates WHERE job_id=? ORDER BY slot",
                    (claim.job_id,),
                )
                final_rows = list(await final_cursor.fetchall())
                if not final_rows or any(
                    str(_row(row, "status", 1) or "")
                    not in {
                        CandidateLedgerStatus.COMMITTED.value,
                        CandidateLedgerStatus.FAILED.value,
                    }
                    or str(_row(row, "disposition", 0) or "")
                    not in _TERMINAL_DISPOSITIONS
                    for row in final_rows
                ):
                    await self.connection.commit()
                    return CompletionResult(
                        True,
                        SummaryJobStatus.RUNNING,
                        0,
                        SummaryReasonCode.LEDGER_UNRESOLVED,
                    )
                counts = {item: 0 for item in _TERMINAL_DISPOSITIONS}
                for row in final_rows:
                    disposition = str(_row(row, "disposition", 0) or "")
                    if disposition not in counts:
                        return await self._mark_unknown_claim(claim, now)
                    counts[disposition] += 1
                if counts[CandidateDisposition.FAILED.value]:
                    return await self._mark_unknown_claim(claim, now)
                values = {
                    "canonical_count": counts[CandidateDisposition.CANONICAL.value],
                    "quarantine_count": counts[CandidateDisposition.QUARANTINED.value],
                    "discard_count": counts[CandidateDisposition.DISCARD.value],
                    "mark_write_count": counts[CandidateDisposition.MARK_WRITE.value],
                    "failed_count": 0,
                    "skipped_count": counts[
                        CandidateDisposition.SKIPPED_IDEMPOTENT.value
                    ],
                }
                updated = await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status='completed',reason_code=?,failed_stage=NULL,
                      lease_until=NULL,claim_token=NULL,canonical_count=?,quarantine_count=?,
                      discard_count=?,mark_write_count=?,failed_count=0,skipped_count=?,updated_at=?
                    WHERE job_id=? AND session_id=? AND session_epoch=? AND status='running'
                      AND claim_token=? AND worker_generation=?
                    """,
                    (
                        SummaryReasonCode.COMPLETED.value,
                        values["canonical_count"],
                        values["quarantine_count"],
                        values["discard_count"],
                        values["mark_write_count"],
                        values["skipped_count"],
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
                old_values = {
                    "canonical_count": int(_row(job, "canonical_count", 2) or 0),
                    "quarantine_count": int(_row(job, "quarantine_count", 3) or 0),
                    "discard_count": int(_row(job, "discard_count", 4) or 0),
                    "mark_write_count": int(_row(job, "mark_write_count", 5) or 0),
                    "failed_count": int(_row(job, "failed_count", 6) or 0),
                    "skipped_count": int(_row(job, "skipped_count", 7) or 0),
                }
                for counter, field in {
                    "canonical_total": "canonical_count",
                    "quarantine_total": "quarantine_count",
                    "discard_total": "discard_count",
                    "mark_write_total": "mark_write_count",
                    "failed_candidate_total": "failed_count",
                    "skipped_idempotent_total": "skipped_count",
                }.items():
                    increment = max(0, values[field] - old_values[field])
                    if increment:
                        await self.connection.execute(
                            "UPDATE summary_task_counters SET value=value+? WHERE counter_name=?",
                            (increment, counter),
                        )
                cursor_value = await self._advance_cursor(
                    claim.session_id, claim.session_epoch, now
                )
                await self.connection.commit()
                return CompletionResult(
                    True,
                    SummaryJobStatus.COMPLETED,
                    cursor_value,
                    SummaryReasonCode.COMPLETED,
                )
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return CompletionResult(
                False, SummaryJobStatus.UNKNOWN, reason_code=SummaryReasonCode.UNKNOWN
            )


__all__ = ["SummaryStoreReconcileMixin"]
