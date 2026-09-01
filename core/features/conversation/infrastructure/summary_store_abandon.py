"""总结任务的管理员 abandoned 收口。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ...reflection.domain.summary_models import SummaryReasonCode


class SummaryStoreAbandonMixin:
    """提供带操作审计和 canonical 保护的 abandoned 转换。"""

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...

        async def _begin_summary(self) -> None: ...
        async def _rollback_summary(self) -> None: ...
        async def _advance_cursor(
            self, session_id: str, epoch: int, now: float
        ) -> int: ...

    async def confirm_abandon_session_jobs(self, session_id: str, epoch: int) -> int:
        """管理员确认数据丢失后跳过无 canonical 证据的阻塞任务。"""
        if self.connection is None:
            return 0
        reason = SummaryReasonCode.ABANDONED_CONFIRMED.value
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                eligible_cursor = await self.connection.execute(
                    """
                    SELECT job_id FROM summary_jobs AS job
                    WHERE session_id=? AND session_epoch=?
                      AND status IN ('blocked','unknown')
                      AND canonical_count=0
                      AND NOT EXISTS (
                        SELECT 1 FROM summary_job_candidates AS c
                        WHERE c.job_id=job.job_id
                          AND (
                            c.canonical_id IS NOT NULL
                            OR c.disposition IN (
                              'canonical','mark_write','skipped_idempotent'
                            )
                            OR (c.status='committed' AND c.disposition IS NULL)
                          )
                      )
                    ORDER BY start_seq,job_id
                    """,
                    (session_id, epoch),
                )
                job_ids = [
                    str(row["job_id"]) for row in await eligible_cursor.fetchall()
                ]
                confirmed = 0
                totals = {
                    "canonical_total": 0,
                    "quarantine_total": 0,
                    "discard_total": 0,
                    "mark_write_total": 0,
                    "failed_candidate_total": 0,
                    "skipped_idempotent_total": 0,
                }
                for job_id in job_ids:
                    await self.connection.execute(
                        """
                        UPDATE summary_job_candidates
                        SET status='failed',disposition='failed',canonical_id=NULL,
                          updated_at=?
                        WHERE job_id=? AND canonical_id IS NULL
                          AND status IN ('planned','writing','unknown')
                        """,
                        (now, job_id),
                    )
                    updated = await self.connection.execute(
                        """
                        UPDATE summary_jobs
                        SET status='abandoned',lease_until=NULL,claim_token=NULL,
                          failed_stage='operator_confirmed',reason_code=?,
                          operator_action='admin_confirmed',canonical_count=0,
                          quarantine_count=(SELECT COUNT(*) FROM summary_job_candidates c WHERE c.job_id=summary_jobs.job_id AND c.disposition='quarantined'),
                          discard_count=(SELECT COUNT(*) FROM summary_job_candidates c WHERE c.job_id=summary_jobs.job_id AND c.disposition='discard'),
                          mark_write_count=0,
                          failed_count=(SELECT COUNT(*) FROM summary_job_candidates c WHERE c.job_id=summary_jobs.job_id AND c.disposition='failed'),
                          skipped_count=0,updated_at=?
                        WHERE job_id=? AND session_id=? AND session_epoch=?
                          AND status IN ('blocked','unknown')
                        """,
                        (reason, now, job_id, session_id, epoch),
                    )
                    if updated.rowcount != 1:
                        continue
                    confirmed += 1
                    counts_cursor = await self.connection.execute(
                        """
                        SELECT
                          SUM(CASE WHEN disposition='canonical' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN disposition='quarantined' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN disposition='discard' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN disposition='mark_write' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN disposition='failed' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN disposition='skipped_idempotent' THEN 1 ELSE 0 END)
                        FROM summary_job_candidates WHERE job_id=?
                        """,
                        (job_id,),
                    )
                    row = await counts_cursor.fetchone()
                    for counter, value in zip(
                        totals,
                        tuple(row) if row is not None else (0,) * len(totals),
                        strict=True,
                    ):
                        totals[counter] += max(0, int(value or 0))
                for counter, increment in totals.items():
                    if increment:
                        await self.connection.execute(
                            "UPDATE summary_task_counters SET value=value+? WHERE counter_name=?",
                            (increment, counter),
                        )
                if confirmed:
                    await self._advance_cursor(session_id, epoch, now)
                await self.connection.commit()
                return confirmed
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            raise RuntimeError("summary_abandon_failed") from error


__all__ = ["SummaryStoreAbandonMixin"]
