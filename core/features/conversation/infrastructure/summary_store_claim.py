"""ConversationStore 的总结任务领取与候选 intent 持久化。"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from ....shared.summary_source_fence import SummarySourceFence
from ...reflection.domain.summary_models import (
    CandidateIntent,
    ClaimedJob,
    SummaryJob,
    SummaryJobStatus,
    SummaryReasonCode,
)
from .summary_store_keys import owned_slot_key


def _timestamp(value: datetime | float | None = None) -> float:
    """把可控时间值转换为非负 Unix 秒。"""
    if value is None:
        return max(0.0, time.time())
    return max(0.0, value.timestamp() if isinstance(value, datetime) else float(value))


def _row(row: Any, name: str, index: int) -> Any:
    """兼容 sqlite Row 和 tuple 测试替身。"""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return row[index]


class SummaryStoreClaimMixin:
    """提供 ready 任务领取、claim fencing 与候选 slot intent 写入。"""

    if TYPE_CHECKING:
        connection: Any
        _write_lock: asyncio.Lock

        def _summary_now(self) -> float: ...

        async def _begin_summary(self) -> None: ...

        async def _rollback_summary(self) -> None: ...

    async def _job(self, row: Any) -> SummaryJob:
        """将 summary_jobs 行映射为安全 DTO。"""
        return SummaryJob(
            job_id=str(_row(row, "job_id", 0)),
            session_id=str(_row(row, "session_id", 1)),
            session_epoch=int(_row(row, "session_epoch", 2)),
            start_seq=int(_row(row, "start_seq", 3)),
            end_seq=int(_row(row, "end_seq", 4)),
            expected_count=int(_row(row, "expected_count", 5)),
            source_digest=str(_row(row, "source_digest", 6)),
            status=SummaryJobStatus(str(_row(row, "status", 14))),
            persona_id=_row(row, "persona_id", 7),
            chat_type=_row(row, "chat_type", 8),
            group_id=_row(row, "group_id", 9),
            scope_id=_row(row, "scope_id", 10),
            gate_revision=str(_row(row, "gate_revision", 11) or ""),
            gate_snapshot_json=str(_row(row, "gate_snapshot_json", 12) or "{}"),
            triggered_by=str(_row(row, "triggered_by", 13)),
            attempt_count=int(_row(row, "attempt_count", 15) or 0),
            next_attempt_at=float(_row(row, "next_attempt_at", 16) or 0),
            lease_until=_row(row, "lease_until", 17),
            worker_generation=int(_row(row, "worker_generation", 18) or 0),
            failed_stage=_row(row, "failed_stage", 19),
            reason_code=cast(
                SummaryReasonCode,
                str(_row(row, "reason_code", 20) or "unknown"),
            ),
            exception_type=_row(row, "exception_type", 21),
            operator_action=_row(row, "operator_action", 30),
            canonical_count=int(_row(row, "canonical_count", 22) or 0),
            quarantine_count=int(_row(row, "quarantine_count", 23) or 0),
            discard_count=int(_row(row, "discard_count", 24) or 0),
            mark_write_count=int(_row(row, "mark_write_count", 25) or 0),
            failed_count=int(_row(row, "failed_count", 26) or 0),
            skipped_count=int(_row(row, "skipped_count", 27) or 0),
            created_at=float(_row(row, "created_at", 28) or 0),
            updated_at=float(_row(row, "updated_at", 29) or 0),
        )

    async def claim_ready(
        self,
        now: datetime | float | None,
        scheduler_id: str,
        limit: int,
        *,
        max_parallel_per_session: int = 1,
        lease_seconds: int = 120,
        session_order: Sequence[str] | None = None,
        round_robin_after: str | None = None,
        global_limit: int | None = None,
    ) -> list[ClaimedJob]:
        """按 Store 时钟域和 session 顺序领取 ready 任务并设置 fencing。"""
        if self.connection is None or limit <= 0:
            return []
        stamp = self._summary_now() if now is None else _timestamp(now)
        order = {str(item): index for index, item in enumerate(session_order or ())}
        after = str(round_robin_after) if round_robin_after else None
        try:
            async with self._write_lock:
                await self._begin_summary()
                cursor_meta = await self.connection.execute(
                    "SELECT meta_value FROM summary_store_meta WHERE meta_key=?",
                    ("summary_round_robin_cursor",),
                )
                persisted_after_row = await cursor_meta.fetchone()
                persisted_after = (
                    str(_row(persisted_after_row, "meta_value", 0) or "")
                    if persisted_after_row is not None
                    else ""
                )
                after = persisted_after or after
                cursor = await self.connection.execute(
                    """
                    SELECT job.*
                    FROM summary_jobs AS job
                    INNER JOIN session_epochs AS epoch
                      ON epoch.session_id=job.session_id
                     AND epoch.epoch=job.session_epoch
                    WHERE job.status IN ('queued','failed')
                      AND job.attempt_count < 3
                      AND job.next_attempt_at<=?
                    """,
                    (stamp,),
                )
                rows = list(await cursor.fetchall())
                rows.sort(
                    key=lambda item: (
                        (0 if str(_row(item, "session_id", 1)) > after else 1)
                        if after is not None
                        else order.get(str(_row(item, "session_id", 1)), len(order)),
                        str(_row(item, "session_id", 1)),
                        int(_row(item, "start_seq", 3)),
                        float(_row(item, "created_at", 28) or 0),
                        str(_row(item, "job_id", 0)),
                    )
                )
                active_cursor = await self.connection.execute(
                    """
                    SELECT job.session_id,COUNT(*) AS active
                    FROM summary_jobs AS job
                    INNER JOIN session_epochs AS epoch
                      ON epoch.session_id=job.session_id
                     AND epoch.epoch=job.session_epoch
                    WHERE job.status='running' AND job.lease_until IS NOT NULL
                      AND job.lease_until>?
                    GROUP BY job.session_id
                    """,
                    (stamp,),
                )
                active = {
                    str(_row(item, "session_id", 0)): int(_row(item, "active", 1) or 0)
                    for item in await active_cursor.fetchall()
                }
                available_global = (
                    None
                    if global_limit is None
                    else max(0, int(global_limit) - sum(active.values()))
                )
                claims: list[ClaimedJob] = []
                claimed_sessions: set[str] = set()
                for item in rows:
                    if len(claims) >= limit or (
                        available_global is not None and len(claims) >= available_global
                    ):
                        break
                    session_id = str(_row(item, "session_id", 1))
                    if session_id in claimed_sessions:
                        continue
                    if active.get(session_id, 0) >= max(
                        1, int(max_parallel_per_session)
                    ):
                        continue
                    job_id = str(_row(item, "job_id", 0))
                    token = secrets.token_urlsafe(24)
                    generation = int(_row(item, "worker_generation", 18) or 0) + 1
                    lease_until = stamp + max(1, int(lease_seconds))
                    updated = await self.connection.execute(
                        """
                        UPDATE summary_jobs SET status='running',attempt_count=attempt_count+1,
                          claim_token=?,lease_until=?,worker_generation=?,updated_at=?
                        WHERE job_id=? AND session_id=? AND session_epoch=?
                          AND status IN ('queued','failed') AND attempt_count < 3
                          AND next_attempt_at<=?
                        """,
                        (
                            token,
                            lease_until,
                            generation,
                            stamp,
                            job_id,
                            session_id,
                            int(_row(item, "session_epoch", 2)),
                            stamp,
                        ),
                    )
                    if updated.rowcount != 1:
                        continue
                    refreshed = await self.connection.execute(
                        "SELECT * FROM summary_jobs WHERE job_id=?", (job_id,)
                    )
                    fresh = await refreshed.fetchone()
                    if fresh is None:
                        continue
                    try:
                        job = await self._job(fresh)
                    except (TypeError, ValueError):
                        invalid = await self.connection.execute(
                            """
                            UPDATE summary_jobs
                            SET status='blocked',failed_stage='claim_validate',
                              reason_code='blocked',claim_token=NULL,lease_until=NULL,
                              updated_at=?
                            WHERE job_id=? AND status='running' AND claim_token=?
                            """,
                            (stamp, job_id, token),
                        )
                        if invalid.rowcount != 1:
                            await self._rollback_summary()
                            raise RuntimeError("summary_claim_failed")
                        continue
                    claims.append(
                        ClaimedJob(
                            job,
                            token,
                            scheduler_id,
                            lease_until,
                            generation,
                        )
                    )
                    claimed_sessions.add(session_id)
                    active[session_id] = active.get(session_id, 0) + 1
                if claims:
                    await self.connection.execute(
                        """
                        INSERT INTO summary_store_meta(meta_key,meta_value)
                        VALUES (?,?)
                        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
                        """,
                        ("summary_round_robin_cursor", claims[-1].session_id),
                    )
                await self.connection.commit()
                return claims
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            raise RuntimeError("summary_claim_failed") from error

    async def _claim_matches(self, claim: ClaimedJob) -> bool:
        """检查 claim token、来源范围、epoch、generation 和 lease 是否仍有效。"""
        cursor = await self.connection.execute(
            """
            SELECT 1
            FROM summary_jobs AS job
            INNER JOIN session_epochs AS epoch
              ON epoch.session_id=job.session_id AND epoch.epoch=job.session_epoch
            WHERE job.job_id=? AND job.session_id=? AND job.session_epoch=?
              AND job.start_seq=? AND job.end_seq=? AND job.expected_count=?
              AND job.source_digest=?
              AND job.status='running' AND job.claim_token=?
              AND job.worker_generation=? AND job.lease_until IS NOT NULL
              AND job.lease_until > ?
            """,
            (
                claim.job_id,
                claim.session_id,
                claim.session_epoch,
                claim.start_seq,
                claim.end_seq,
                claim.expected_count,
                claim.source_digest,
                claim.claim_token,
                claim.worker_generation,
                self._summary_now(),
            ),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def claim_is_active(self, claim: ClaimedJob) -> bool:
        """检查 claim 是否仍可执行外部副作用。"""
        if self.connection is None:
            return False
        return await self._claim_matches(claim)

    async def summary_source_fence_is_active(
        self, source_fence: SummarySourceFence
    ) -> bool:
        """验证来源 fence 仍指向同一未过期 running claim。"""

        if self.connection is None or not isinstance(source_fence, SummarySourceFence):
            return False
        cursor = await self.connection.execute(
            """
            SELECT 1
            FROM summary_jobs AS job
            INNER JOIN session_epochs AS epoch
              ON epoch.session_id=job.session_id AND epoch.epoch=job.session_epoch
            WHERE job.job_id=? AND job.session_id=? AND job.session_epoch=?
              AND job.start_seq=? AND job.end_seq=? AND job.expected_count=?
              AND job.source_digest=? AND job.status='running'
              AND job.claim_token=? AND job.worker_generation=?
              AND job.lease_until IS NOT NULL AND job.lease_until > ?
            """,
            (
                source_fence.job_id,
                source_fence.session_id,
                source_fence.session_epoch,
                source_fence.start_seq,
                source_fence.end_seq,
                source_fence.expected_count,
                source_fence.source_digest,
                source_fence.claim_token,
                source_fence.worker_generation,
                self._summary_now(),
            ),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def begin_candidate_intents(
        self, claim: ClaimedJob, intents: Sequence[CandidateIntent]
    ) -> bool:
        """保存候选 slot intent，并拒绝同 slot 摘要变化。"""
        if self.connection is None:
            return False
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                if not await self._claim_matches(claim):
                    await self._rollback_summary()
                    return False
                normalized: dict[int, tuple[str, CandidateIntent]] = {}
                for intent in intents:
                    if (
                        not isinstance(intent, CandidateIntent)
                        or intent.slot in normalized
                        or len(intent.idempotency_key) > 256
                    ):
                        await self._rollback_summary()
                        return False
                    slot_key = await owned_slot_key(
                        self.connection,
                        claim.job_id,
                        intent.slot,
                        intent.content_digest,
                    )
                    normalized[intent.slot] = (slot_key, intent)
                ledger_cursor = await self.connection.execute(
                    "SELECT slot,slot_key,content_digest,idempotency_key "
                    "FROM summary_job_candidates WHERE job_id=?",
                    (claim.job_id,),
                )
                ledger_rows = await ledger_cursor.fetchall()
                existing = {
                    int(_row(row, "slot", 0)): (
                        _row(row, "slot_key", 1),
                        _row(row, "content_digest", 2),
                        str(_row(row, "idempotency_key", 3) or ""),
                    )
                    for row in ledger_rows
                }
                if existing and set(existing) != set(normalized):
                    await self._rollback_summary()
                    return False
                for slot, (slot_key, intent) in normalized.items():
                    expected = (slot_key, intent.content_digest, intent.idempotency_key)
                    if slot in existing:
                        if existing[slot] != expected:
                            await self._rollback_summary()
                            return False
                        continue
                    await self.connection.execute(
                        """
                        INSERT INTO summary_job_candidates
                          (job_id,slot,slot_key,content_digest,idempotency_key,
                           disposition,status,canonical_id,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            claim.job_id,
                            intent.slot,
                            slot_key,
                            intent.content_digest,
                            intent.idempotency_key,
                            intent.disposition.value if intent.disposition else None,
                            intent.status.value,
                            intent.canonical_id,
                            now,
                        ),
                    )
                await self.connection.commit()
                return True
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return False

    async def begin_candidate_write(
        self, claim: ClaimedJob, intent: CandidateIntent
    ) -> bool:
        """在单条候选外部副作用前把 slot 原子标记为 writing。

        已经收口的 ``committed``/``failed`` slot 保持原状态，供崩溃重试的
        幂等查询复用；``planned`` 只能在仍属于当前 claim 时进入 writing。
        """

        if self.connection is None or not isinstance(intent, CandidateIntent):
            return False
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                if not await self._claim_matches(claim):
                    await self._rollback_summary()
                    return False
                slot_key = await owned_slot_key(
                    self.connection,
                    claim.job_id,
                    intent.slot,
                    intent.content_digest,
                )
                cursor = await self.connection.execute(
                    """
                    UPDATE summary_job_candidates
                    SET status=CASE WHEN status='planned' THEN 'writing' ELSE status END,
                        updated_at=?
                    WHERE job_id=? AND slot=? AND slot_key=? AND content_digest=?
                      AND idempotency_key=?
                      AND status IN ('planned','writing','committed','failed')
                    """,
                    (
                        now,
                        claim.job_id,
                        intent.slot,
                        slot_key,
                        intent.content_digest,
                        intent.idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    await self._rollback_summary()
                    return False
                await self.connection.commit()
                return True
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return False


__all__ = ["SummaryStoreClaimMixin"]
