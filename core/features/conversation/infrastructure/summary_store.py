"""ConversationStore 的总结任务与稳定消息来源持久化。"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ....shared.contracts.conversation import Message
from ...reflection.domain.summary_models import (
    CandidateIntent,
    ClaimedJob,
    SourceWindow,
    SummaryEnqueueResult,
    SummaryJob,
    SummaryJobStatus,
    SummaryReasonCode,
    SummaryWindowContext,
    source_window_digest,
)
from .summary_legacy import SummaryLegacyMigrationMixin
from .summary_store_keys import owned_slot_key, source_guarded
from .summary_store_terminal import SummaryStoreTerminalMixin


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


def _message(row: Any) -> Message:
    """将内部消息行转换为不含 message_seq 的公开 Message。"""
    return Message.from_dict(
        {
            "id": _row(row, "id", 0),
            "session_id": _row(row, "session_id", 1),
            "role": _row(row, "role", 2),
            "content": _row(row, "content", 3),
            "sender_id": _row(row, "sender_id", 4),
            "sender_name": _row(row, "sender_name", 5),
            "group_id": _row(row, "group_id", 6),
            "platform": _row(row, "platform", 7),
            "timestamp": _row(row, "timestamp", 8),
            "metadata": _row(row, "metadata", 9),
        }
    )


def _rows_digest(rows: Sequence[Any]) -> str:
    """按消息公共字段和内部序号计算来源摘要。"""
    messages = tuple(_message(item) for item in rows)
    seqs = tuple(int(_row(item, "message_seq", 10)) for item in rows)
    return source_window_digest(messages, seqs)


class SummaryStoreMixin(SummaryLegacyMigrationMixin, SummaryStoreTerminalMixin):
    """为 ConversationStore 增加总结任务 Store port 实现。"""

    connection: Any
    _write_lock: Any
    _summary_clock: Callable[[], datetime | float] = time.time

    def set_summary_clock(self, clock: Callable[[], datetime | float]) -> None:
        """设置总结任务统一使用的可控时钟。"""
        self._summary_clock = clock

    def _summary_now(self) -> float:
        """返回总结状态比较使用的统一 Unix 时间。"""
        return _timestamp(self._summary_clock())

    if TYPE_CHECKING:

        async def _claim_matches(self, claim: ClaimedJob) -> bool: ...

        async def _ensure_epoch(
            self, session_id: str, now: float
        ) -> tuple[int, int]: ...

    async def _begin_summary(self) -> None:
        """开始总结状态的立即事务。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        await self.connection.execute("BEGIN IMMEDIATE")

    async def _rollback_summary(self) -> None:
        """尽力回滚事务且不覆盖原始错误。"""
        connection = getattr(self, "connection", None)
        if connection is None:
            return
        try:
            await connection.rollback()
        except BaseException:
            return

    async def _ensure_epoch(self, session_id: str, now: float) -> tuple[int, int]:
        """在事务中读取或创建 session epoch 与 cursor。"""
        cursor = await self.connection.execute(
            "SELECT epoch, cursor_seq FROM session_epochs WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is not None:
            return int(_row(row, "epoch", 0)), int(_row(row, "cursor_seq", 1) or 0)
        await self.connection.execute(
            "INSERT INTO session_epochs(session_id,epoch,cursor_seq,updated_at) VALUES (?,1,0,?)",
            (session_id, now),
        )
        return 1, 0

    async def plan_existing_frontiers(
        self,
        context_factory: Callable[[str, int, int], SummaryWindowContext],
    ) -> int:
        """为已有会话按同一 planner 补建启动期总结窗口。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        cursor = await self.connection.execute(
            "SELECT session_id FROM sessions WHERE session_id <> '' ORDER BY session_id"
        )
        session_ids = [
            str(_row(row, "session_id", 0)) for row in await cursor.fetchall()
        ]
        queued = 0
        for session_id in session_ids:
            epoch, summary_cursor = await self.get_summary_epoch(session_id)
            observed_end = await self.get_message_seq_end(session_id)
            context = context_factory(session_id, epoch, summary_cursor)
            if not isinstance(context, SummaryWindowContext):
                raise TypeError("启动总结上下文类型不正确")
            result = await self.plan_and_enqueue_windows(context, observed_end)
            queued += result.queued
        return queued

    async def get_message_seq_end(self, session_id: str) -> int:
        """返回指定会话当前最大的稳定 message_seq。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(message_seq), 0) FROM messages WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0

    @source_guarded
    async def clear_session_atomically(self, session_id: str) -> int:
        """在一次 Store 事务中 fence epoch、取消任务并删除会话消息。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                epoch, _ = await self._ensure_epoch(session_id, now)
                await self.connection.execute(
                    """
                    UPDATE summary_jobs SET status='cancelled', claim_token=NULL,
                      lease_until=NULL, reason_code='epoch_fenced', updated_at=?
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
                    "UPDATE sessions SET message_count=0, metadata='{}' WHERE session_id=?",
                    (session_id,),
                )
                await self.connection.execute(
                    """
                    UPDATE session_epochs SET epoch=?, cursor_seq=0,
                      pending_summary_json=NULL, tombstoned_at=?, updated_at=?
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

    async def get_summary_epoch(self, session_id: str) -> tuple[int, int]:
        """读取当前 session epoch 和连续总结游标。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        cursor = await self.connection.execute(
            "SELECT epoch,cursor_seq FROM session_epochs WHERE session_id=?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return (
            (1, 0)
            if row is None
            else (
                int(_row(row, "epoch", 0)),
                int(_row(row, "cursor_seq", 1) or 0),
            )
        )

    async def _source_rows(
        self, session_id: str, start_seq: int, end_seq: int
    ) -> list[Any]:
        """按 message_seq 读取固定来源行。"""
        cursor = await self.connection.execute(
            """
            SELECT id,session_id,role,content,sender_id,sender_name,group_id,
                   platform,timestamp,metadata,message_seq
            FROM messages WHERE session_id=? AND message_seq>? AND message_seq<=?
            ORDER BY message_seq ASC
            """,
            (session_id, start_seq, end_seq),
        )
        return list(await cursor.fetchall())

    async def plan_and_enqueue_windows(
        self, context: SummaryWindowContext, observed_end_seq: int
    ) -> SummaryEnqueueResult:
        """在一个事务中按 frontier 规划固定窗口并幂等入队。"""
        if (
            isinstance(observed_end_seq, bool)
            or not isinstance(observed_end_seq, int)
            or observed_end_seq < 0
        ):
            return SummaryEnqueueResult(
                False, reason_code=SummaryReasonCode.SOURCE_INCOMPLETE
            )
        now = self._summary_now()
        try:
            async with self._write_lock:
                await self._begin_summary()
                epoch, cursor_seq = await self._ensure_epoch(context.session_id, now)
                # 上层只能从当前连续游标规划；接受更大的 start 会静默跳过来源缺口。
                if epoch != context.session_epoch or cursor_seq != context.start_seq:
                    await self._rollback_summary()
                    return SummaryEnqueueResult(
                        False, reason_code=SummaryReasonCode.EPOCH_FENCED
                    )
                frontier_cursor = await self.connection.execute(
                    """
                    SELECT COALESCE(MAX(end_seq),?) FROM summary_jobs
                    WHERE session_id=? AND session_epoch=?
                    """,
                    (cursor_seq, context.session_id, epoch),
                )
                frontier_row = await frontier_cursor.fetchone()
                frontier = max(cursor_seq, int(frontier_row[0] or 0))
                start = max(frontier, context.start_seq)
                queued = 0
                duplicates = 0
                if start >= observed_end_seq:
                    duplicate_cursor = await self.connection.execute(
                        """
                        SELECT COUNT(*) FROM summary_jobs
                        WHERE session_id=? AND session_epoch=?
                          AND start_seq>=? AND end_seq<=?
                        """,
                        (
                            context.session_id,
                            epoch,
                            context.start_seq,
                            observed_end_seq,
                        ),
                    )
                    duplicates = int((await duplicate_cursor.fetchone())[0] or 0)
                size = max(2, int(context.window_size))
                ranges: list[tuple[int, int]] = []
                while start + size <= observed_end_seq:
                    ranges.append((start, start + size))
                    start += size
                if observed_end_seq - start >= 2:
                    ranges.append((start, int(observed_end_seq)))
                for start_seq, end_seq in ranges:
                    overlap_cursor = await self.connection.execute(
                        """
                        SELECT 1 FROM summary_jobs
                        WHERE session_id=? AND session_epoch=? AND start_seq<? AND end_seq>?
                        LIMIT 1
                        """,
                        (context.session_id, epoch, end_seq, start_seq),
                    )
                    if await overlap_cursor.fetchone() is not None:
                        duplicates += 1
                        continue
                    source_rows = await self._source_rows(
                        context.session_id, start_seq, end_seq
                    )
                    expected = end_seq - start_seq
                    complete = len(source_rows) == expected and tuple(
                        int(_row(item, "message_seq", 10)) for item in source_rows
                    ) == tuple(range(start_seq + 1, end_seq + 1))
                    if complete:
                        digest = _rows_digest(source_rows)
                        status = SummaryJobStatus.QUEUED.value
                        reason = SummaryReasonCode.QUEUED.value
                        queued += 1
                    else:
                        digest = hashlib.sha256(
                            f"incomplete:{context.session_id}:{start_seq}:{end_seq}".encode()
                        ).hexdigest()
                        status = SummaryJobStatus.BLOCKED.value
                        reason = SummaryReasonCode.SOURCE_INCOMPLETE.value
                    await self.connection.execute(
                        """
                        INSERT INTO summary_jobs(
                          job_id,session_id,session_epoch,start_seq,end_seq,expected_count,
                          source_digest,persona_id,chat_type,group_id,scope_id,gate_revision,
                          gate_snapshot_json,triggered_by,status,attempt_count,next_attempt_at,
                          worker_generation,reason_code,created_at,updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uuid.uuid4().hex,
                            context.session_id,
                            epoch,
                            start_seq,
                            end_seq,
                            expected,
                            digest,
                            context.persona_id,
                            context.chat_type,
                            context.group_id,
                            context.scope_id,
                            context.gate_revision,
                            context.gate_snapshot_json,
                            context.triggered_by,
                            status,
                            0,
                            now,
                            0,
                            reason,
                            now,
                            now,
                        ),
                    )
                executable_cursor = await self.connection.execute(
                    "SELECT COUNT(*) FROM summary_jobs WHERE status IN ('queued','failed') AND next_attempt_at<=?",
                    (now,),
                )
                executable = int((await executable_cursor.fetchone())[0] or 0)
                active = await self._active_count()
                await self._advance_cursor(context.session_id, epoch, now)
                await self.connection.commit()
                return SummaryEnqueueResult(
                    bool(queued or duplicates),
                    queued=queued,
                    duplicates=duplicates,
                    active_parallelism=active,
                    target_parallelism=executable,
                    reason_code=(
                        SummaryReasonCode.QUEUED
                        if queued
                        else SummaryReasonCode.DUPLICATE
                        if duplicates
                        else SummaryReasonCode.NO_WINDOW
                    ),
                )
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return SummaryEnqueueResult(
                False, reason_code=SummaryReasonCode.STORE_UNAVAILABLE
            )

    async def _active_count(self) -> int:
        """读取仍在租约内的 running 任务数量。"""
        cursor = await self.connection.execute(
            "SELECT COUNT(*) FROM summary_jobs WHERE status='running' AND lease_until > ?",
            (self._summary_now(),),
        )
        return int((await cursor.fetchone())[0] or 0)

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
            reason_code=SummaryReasonCode(
                str(_row(row, "reason_code", 20) or "unknown")
            ),
            exception_type=_row(row, "exception_type", 21),
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
        now: datetime,
        scheduler_id: str,
        limit: int,
        *,
        max_parallel_per_session: int = 1,
        lease_seconds: int = 120,
        session_order: Sequence[str] | None = None,
        global_limit: int | None = None,
    ) -> list[ClaimedJob]:
        """按 session 顺序领取 ready 任务并设置 claim fencing。"""
        if self.connection is None or limit <= 0:
            return []
        stamp = _timestamp(now)
        order = {str(item): index for index, item in enumerate(session_order or ())}
        try:
            async with self._write_lock:
                await self._begin_summary()
                cursor = await self.connection.execute(
                    """
                    SELECT * FROM summary_jobs
                    WHERE status IN ('queued','failed') AND next_attempt_at<=?
                    ORDER BY start_seq,created_at,job_id
                    """,
                    (stamp,),
                )
                rows = list(await cursor.fetchall())
                rows.sort(
                    key=lambda item: (
                        order.get(str(_row(item, "session_id", 1)), len(order)),
                        int(_row(item, "start_seq", 3)),
                        float(_row(item, "created_at", 28) or 0),
                        str(_row(item, "job_id", 0)),
                    )
                )
                active_cursor = await self.connection.execute(
                    """
                    SELECT session_id,COUNT(*) AS active
                    FROM summary_jobs
                    WHERE status='running' AND lease_until IS NOT NULL AND lease_until>?
                    GROUP BY session_id
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
                          AND status IN ('queued','failed') AND next_attempt_at<=?
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
                    claims.append(
                        ClaimedJob(
                            await self._job(fresh),
                            token,
                            scheduler_id,
                            lease_until,
                            generation,
                        )
                    )
                    claimed_sessions.add(session_id)
                    active[session_id] = active.get(session_id, 0) + 1
                await self.connection.commit()
                return claims
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception:
            await self._rollback_summary()
            return []

    async def _claim_matches(self, claim: ClaimedJob) -> bool:
        """检查 claim token、epoch、generation 和 lease 是否仍有效。"""
        cursor = await self.connection.execute(
            """
            SELECT 1 FROM summary_jobs WHERE job_id=? AND session_id=? AND session_epoch=?
              AND status='running' AND claim_token=? AND worker_generation=?
              AND lease_until IS NOT NULL AND lease_until > ?
            """,
            (
                claim.job_id,
                claim.session_id,
                claim.session_epoch,
                claim.claim_token,
                claim.worker_generation,
                self._summary_now(),
            ),
        )
        return await cursor.fetchone() is not None

    async def claim_is_active(self, claim: ClaimedJob) -> bool:
        """检查 claim 是否仍可执行外部副作用。"""
        if self.connection is None:
            return False
        return await self._claim_matches(claim)

    async def read_claimed_window(self, claim: ClaimedJob) -> SourceWindow:
        """读取当前 claim 的来源并严格验证摘要。"""
        if self.connection is None or not await self._claim_matches(claim):
            raise RuntimeError(SummaryReasonCode.CLAIM_LOST.value)
        rows = await self._source_rows(claim.session_id, claim.start_seq, claim.end_seq)
        messages = tuple(_message(item) for item in rows)
        seqs = tuple(int(_row(item, "message_seq", 10)) for item in rows)
        return SourceWindow(
            session_id=claim.session_id,
            start_seq=claim.start_seq,
            end_seq=claim.end_seq,
            expected_count=claim.expected_count,
            source_digest=claim.source_digest,
            messages=messages,
            message_seqs=seqs,
        )

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
                for intent in intents:
                    if not isinstance(intent, CandidateIntent):
                        await self._rollback_summary()
                        return False
                    slot_key = await owned_slot_key(
                        self.connection,
                        claim.job_id,
                        intent.slot,
                        intent.content_digest,
                    )
                    cursor = await self.connection.execute(
                        "SELECT slot_key,content_digest FROM summary_job_candidates WHERE job_id=? AND slot=?",
                        (claim.job_id, intent.slot),
                    )
                    row = await cursor.fetchone()
                    if row is not None:
                        if (
                            _row(row, "slot_key", 0),
                            _row(row, "content_digest", 1),
                        ) != (slot_key, intent.content_digest):
                            await self._rollback_summary()
                            return False
                        continue
                    await self.connection.execute(
                        """
                        INSERT INTO summary_job_candidates
                          (job_id,slot,slot_key,content_digest,disposition,status,canonical_id,updated_at)
                        VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            claim.job_id,
                            intent.slot,
                            slot_key,
                            intent.content_digest,
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


__all__ = ["SummaryStoreMixin"]
