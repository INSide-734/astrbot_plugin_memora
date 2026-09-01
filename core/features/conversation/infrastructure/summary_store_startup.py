"""总结任务启动期候选 ledger 对账。"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ...reflection.domain.summary_models import (
    CandidateDisposition,
    CandidateLedgerStatus,
    SummaryJobStatus,
)

if TYPE_CHECKING:
    pass


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


class SummaryStoreStartupMixin:
    """扫描崩溃遗留的 writing/unknown slot，并以保守语义恢复。"""

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...

        async def _begin_summary(self) -> None: ...

        async def _rollback_summary(self) -> None: ...

        async def _advance_cursor(
            self, session_id: str, epoch: int, now: float
        ) -> int: ...

    def set_summary_canonical_owner_lookup(
        self, lookup: Callable[[str], int | None | Awaitable[int | None]]
    ) -> None:
        """注入只按幂等键查询 canonical owner 的恢复回调。"""

        self._summary_canonical_owner_lookup = lookup

    async def reconcile_startup_candidates(self) -> int:
        """启动期对账候选副作用，无法确认时固定为 unknown。

        对账不持有 ConversationStore 事务执行外部数据库查询。已发现的正整数
        owner 才能把 slot 收口为 canonical；没有 owner 的 writing/unknown 保留
        unknown，并清除旧 claim，确保恢复失败不会推进连续 cursor。
        """

        connection = getattr(self, "connection", None)
        if connection is None:
            return 0
        try:
            cursor = await connection.execute(
                """
                SELECT j.job_id,j.session_id,j.session_epoch,j.status,
                       c.slot,c.idempotency_key,c.status AS candidate_status,
                       c.disposition,c.canonical_id
                FROM summary_jobs AS j
                JOIN summary_job_candidates AS c ON c.job_id=j.job_id
                WHERE c.status IN ('writing','unknown')
                   OR j.status='running'
                ORDER BY j.job_id,c.slot
                """
            )
            rows = list(await cursor.fetchall())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise RuntimeError("summary_recovery_failed") from error

        jobs: dict[str, dict[str, Any]] = {}
        for row in rows:
            job_id = str(row[0])
            job = jobs.setdefault(
                job_id,
                {
                    "session_id": str(row[1]),
                    "epoch": int(row[2]),
                    "status": str(row[3]),
                    "slots": [],
                },
            )
            job["slots"].append(
                {
                    "slot": int(row[4]),
                    "key": str(row[5] or ""),
                    "status": str(row[6] or ""),
                    "disposition": (str(row[7]) if row[7] is not None else None),
                    "canonical_id": row[8],
                }
            )

        lookup = getattr(self, "_summary_canonical_owner_lookup", None)
        owners: dict[tuple[str, int], int] = {}
        if callable(lookup):
            for job_id, job in jobs.items():
                for slot in job["slots"]:
                    key = slot["key"]
                    if not key:
                        continue
                    try:
                        owner = lookup(key)
                        if inspect.isawaitable(owner):
                            owner = await owner
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                    if (
                        isinstance(owner, int)
                        and not isinstance(owner, bool)
                        and owner > 0
                    ):
                        owners[(job_id, slot["slot"])] = owner

        touched = 0
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                for job_id, job in jobs.items():
                    current = await connection.execute(
                        """
                        SELECT session_id,session_epoch,status,
                               canonical_count,quarantine_count,discard_count,
                               mark_write_count,failed_count,skipped_count
                        FROM summary_jobs WHERE job_id=?
                        """,
                        (job_id,),
                    )
                    job_row = await current.fetchone()
                    if job_row is None:
                        continue
                    session_id = str(job_row[0])
                    epoch = int(job_row[1])
                    current_status = str(job_row[2])
                    epoch_row = await (
                        await connection.execute(
                            "SELECT epoch FROM session_epochs WHERE session_id=?",
                            (session_id,),
                        )
                    ).fetchone()
                    if epoch_row is None or int(epoch_row[0]) != epoch:
                        await connection.execute(
                            """
                            UPDATE summary_jobs
                            SET status='unknown',reason_code='epoch_fenced',
                                failed_stage='startup_reconcile',
                                claim_token=NULL,lease_until=NULL,updated_at=?
                            WHERE job_id=?
                            """,
                            (now, job_id),
                        )
                        touched += 1
                        continue

                    slot_cursor = await connection.execute(
                        """
                        SELECT slot,disposition,status,canonical_id
                        FROM summary_job_candidates WHERE job_id=? ORDER BY slot
                        """,
                        (job_id,),
                    )
                    slot_rows = list(await slot_cursor.fetchall())
                    if not slot_rows:
                        if current_status == SummaryJobStatus.RUNNING.value:
                            await connection.execute(
                                """
                                UPDATE summary_jobs
                                SET status='unknown',reason_code='ledger_unresolved',
                                    failed_stage='startup_reconcile',
                                    claim_token=NULL,lease_until=NULL,updated_at=?
                                WHERE job_id=?
                                """,
                                (now, job_id),
                            )
                            touched += 1
                        continue

                    unresolved = False
                    for slot_row in slot_rows:
                        slot = int(slot_row[0])
                        status = str(slot_row[2] or "")
                        if status not in {
                            CandidateLedgerStatus.WRITING.value,
                            CandidateLedgerStatus.UNKNOWN.value,
                        }:
                            continue
                        owner = owners.get((job_id, slot))
                        if owner is None:
                            unresolved = True
                            await connection.execute(
                                """
                                UPDATE summary_job_candidates
                                SET status='unknown',disposition=NULL,canonical_id=NULL,
                                    updated_at=?
                                WHERE job_id=? AND slot=?
                                """,
                                (now, job_id, slot),
                            )
                            continue
                        updated = await connection.execute(
                            """
                            UPDATE summary_job_candidates
                            SET status='committed',disposition='canonical',canonical_id=?,
                                updated_at=?
                            WHERE job_id=? AND slot=?
                              AND status IN ('writing','unknown')
                              AND (canonical_id IS NULL OR canonical_id=?)
                            """,
                            (owner, now, job_id, slot, owner),
                        )
                        if updated.rowcount != 1:
                            unresolved = True

                    refreshed = await connection.execute(
                        """
                        SELECT disposition,status,canonical_id
                        FROM summary_job_candidates WHERE job_id=? ORDER BY slot
                        """,
                        (job_id,),
                    )
                    final_rows = list(await refreshed.fetchall())
                    for row in final_rows:
                        disposition = row[0]
                        status = str(row[1] or "")
                        canonical_id = row[2]
                        if (
                            status
                            not in {
                                CandidateLedgerStatus.COMMITTED.value,
                                CandidateLedgerStatus.FAILED.value,
                            }
                            or disposition not in _TERMINAL_DISPOSITIONS
                            or (
                                disposition in _CANONICAL_DISPOSITIONS
                                and not isinstance(canonical_id, int)
                            )
                            or (
                                disposition not in _CANONICAL_DISPOSITIONS
                                and canonical_id is not None
                            )
                        ):
                            unresolved = True

                    if unresolved:
                        await connection.execute(
                            """
                            UPDATE summary_jobs
                            SET status='unknown',reason_code='ledger_unresolved',
                                failed_stage='startup_reconcile',
                                claim_token=NULL,lease_until=NULL,updated_at=?
                            WHERE job_id=?
                            """,
                            (now, job_id),
                        )
                        touched += 1
                        continue

                    counts = defaultdict(int)
                    for row in final_rows:
                        counts[str(row[0])] += 1
                    if counts[CandidateDisposition.FAILED.value]:
                        continue
                    values = {
                        "canonical_count": counts[CandidateDisposition.CANONICAL.value],
                        "quarantine_count": counts[
                            CandidateDisposition.QUARANTINED.value
                        ],
                        "discard_count": counts[CandidateDisposition.DISCARD.value],
                        "mark_write_count": counts[
                            CandidateDisposition.MARK_WRITE.value
                        ],
                        "failed_count": 0,
                        "skipped_count": counts[
                            CandidateDisposition.SKIPPED_IDEMPOTENT.value
                        ],
                    }
                    updated = await connection.execute(
                        """
                        UPDATE summary_jobs
                        SET status='completed',reason_code='completed',failed_stage=NULL,
                            claim_token=NULL,lease_until=NULL,canonical_count=?,
                            quarantine_count=?,discard_count=?,mark_write_count=?,
                            failed_count=0,skipped_count=?,updated_at=?
                        WHERE job_id=? AND session_id=? AND session_epoch=?
                          AND status IN ('running','unknown','failed','queued')
                        """,
                        (
                            values["canonical_count"],
                            values["quarantine_count"],
                            values["discard_count"],
                            values["mark_write_count"],
                            values["skipped_count"],
                            now,
                            job_id,
                            session_id,
                            epoch,
                        ),
                    )
                    if updated.rowcount != 1:
                        continue
                    old_values = {
                        "canonical_count": int(job_row[3] or 0),
                        "quarantine_count": int(job_row[4] or 0),
                        "discard_count": int(job_row[5] or 0),
                        "mark_write_count": int(job_row[6] or 0),
                        "failed_count": int(job_row[7] or 0),
                        "skipped_count": int(job_row[8] or 0),
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
                            await connection.execute(
                                "UPDATE summary_task_counters SET value=value+? "
                                "WHERE counter_name=?",
                                (increment, counter),
                            )
                    await self._advance_cursor(session_id, epoch, now)
                    touched += 1
                await connection.commit()
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            raise RuntimeError("summary_recovery_failed") from error
        return touched


__all__ = ["SummaryStoreStartupMixin"]
