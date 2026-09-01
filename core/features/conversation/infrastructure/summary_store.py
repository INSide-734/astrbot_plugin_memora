"""ConversationStore 的总结任务与稳定消息来源持久化。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ....shared.contracts.conversation import Message
from ....shared.summary_source import source_window_digest
from ...reflection.domain.summary_models import (
    ClaimedJob,
    SourceWindow,
    SummaryEnqueueResult,
    SummaryJobStatus,
    SummaryReasonCode,
    SummaryWindowContext,
)
from .summary_legacy import SummaryLegacyMigrationMixin
from .summary_store_claim import SummaryStoreClaimMixin
from .summary_store_startup import SummaryStoreStartupMixin
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
    metadata = _row(row, "metadata", 9)
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError as error:
            raise ValueError("source_metadata_invalid") from error
    if not isinstance(metadata, dict):
        raise ValueError("source_metadata_invalid")
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
            "metadata": metadata,
        }
    )


def _rows_digest(rows: Sequence[Any]) -> str:
    """按消息公共字段和内部序号计算来源摘要。"""
    messages = tuple(_message(item) for item in rows)
    seqs = tuple(int(_row(item, "message_seq", 10)) for item in rows)
    return source_window_digest(messages, seqs)


class SummaryStoreMixin(
    SummaryLegacyMigrationMixin,
    SummaryStoreClaimMixin,
    SummaryStoreStartupMixin,
    SummaryStoreTerminalMixin,
):
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

    @staticmethod
    def _scope_text(value: object) -> str | None:
        """校验启动扫描使用的非正文作用域标量。"""
        if value is None:
            return None
        if not isinstance(value, str):
            raise RuntimeError("summary_startup_scope_unavailable")
        normalized = value.strip()
        if not normalized or len(normalized) > 256:
            raise RuntimeError("summary_startup_scope_unavailable")
        return normalized

    async def get_summary_scope(
        self, session_id: str
    ) -> tuple[str, str | None, str, str | None]:
        """从持久消息作用域构造安全的启动总结上下文投影。

        返回 ``(chat_type, group_id, scope_id, persona_id)``。只使用已经
        持久化的群组列和受限 metadata；作用域证据冲突时拒绝启动扫描，
        不猜测会话标识。此协调锁仅覆盖当前插件进程。
        """
        if self.connection is None:
            raise RuntimeError("summary_startup_scope_unavailable")
        session_cursor = await self.connection.execute(
            "SELECT metadata FROM sessions WHERE session_id=?",
            (session_id,),
        )
        session_row = await session_cursor.fetchone()
        if session_row is None:
            raise RuntimeError("summary_startup_scope_unavailable")
        raw_session_metadata = _row(session_row, "metadata", 0) or "{}"
        try:
            session_metadata = json.loads(raw_session_metadata)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("summary_startup_scope_unavailable") from error
        if not isinstance(session_metadata, dict):
            raise RuntimeError("summary_startup_scope_unavailable")

        persona_values: set[str] = set()
        declared_chat = self._scope_text(session_metadata.get("chat_type"))
        declared_group = self._scope_text(session_metadata.get("group_id"))
        declared_scope = self._scope_text(session_metadata.get("scope_id"))
        declared_persona = self._scope_text(session_metadata.get("persona_id"))
        if declared_persona is not None:
            persona_values.add(declared_persona)

        message_cursor = await self.connection.execute(
            "SELECT group_id, metadata FROM messages WHERE session_id=?",
            (session_id,),
        )
        group_values: set[str] = set()
        for row in await message_cursor.fetchall():
            raw_group = _row(row, "group_id", 0)
            if raw_group is not None and str(raw_group).strip():
                group_values.add(str(raw_group).strip())
            raw_metadata = _row(row, "metadata", 1) or "{}"
            try:
                message_metadata = json.loads(raw_metadata)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("summary_startup_scope_unavailable") from error
            if not isinstance(message_metadata, dict):
                raise RuntimeError("summary_startup_scope_unavailable")
            message_persona = self._scope_text(message_metadata.get("persona_id"))
            if message_persona is not None:
                persona_values.add(message_persona)

        if len(group_values) > 1 or len(persona_values) > 1:
            raise RuntimeError("summary_startup_scope_unavailable")
        group_id = next(iter(group_values), None)
        persona_id = next(iter(persona_values), None)
        if group_id is not None:
            if (
                declared_chat == "private"
                or (declared_group is not None and declared_group != group_id)
                or (declared_scope is not None and declared_scope != group_id)
            ):
                raise RuntimeError("summary_startup_scope_unavailable")
            return "group", group_id, group_id, persona_id

        if declared_chat == "group" or declared_group is not None:
            raise RuntimeError("summary_startup_scope_unavailable")
        if declared_scope is not None and declared_scope != session_id:
            raise RuntimeError("summary_startup_scope_unavailable")
        return "private", None, session_id, persona_id

    async def plan_existing_frontiers(
        self,
        context_factory: Callable[
            [str, int, int], SummaryWindowContext | Awaitable[SummaryWindowContext]
        ],
    ) -> int:
        """为所有已有会话通过同一 frontier planner 补建窗口。"""
        if self.connection is None:
            raise RuntimeError("summary_recovery_failed")
        cursor = await self.connection.execute(
            "SELECT session_id FROM sessions WHERE session_id <> '' ORDER BY session_id"
        )
        session_ids = [
            str(_row(row, "session_id", 0)) for row in await cursor.fetchall()
        ]
        queued = 0
        for session_id in session_ids:
            epoch, summary_cursor = await self.get_summary_epoch(session_id)
            context = context_factory(session_id, epoch, summary_cursor)
            if inspect.isawaitable(context):
                context = await context
            if not isinstance(context, SummaryWindowContext):
                raise RuntimeError("summary_startup_context_invalid")
            if (
                context.session_id != session_id
                or context.session_epoch != epoch
                or context.start_seq != summary_cursor
                or context.end_seq != summary_cursor
            ):
                raise RuntimeError("summary_startup_context_invalid")
            observed_end = await self.get_message_seq_end(session_id)
            result = await self.plan_and_enqueue_windows(
                context, observed_end, strict=True
            )
            if not isinstance(result, SummaryEnqueueResult):
                raise RuntimeError("summary_startup_planning_failed")
            queued += result.queued
        return queued

    async def get_message_seq_end(self, session_id: str) -> int:
        """返回消息和连续总结 cursor 共同定义的稳定高水位。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        cursor = await self.connection.execute(
            """
            SELECT MAX(
              COALESCE((SELECT MAX(message_seq) FROM messages WHERE session_id=?),0),
              COALESCE((SELECT cursor_seq FROM session_epochs WHERE session_id=?),0)
            )
            """,
            (session_id, session_id),
        )
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0

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

    async def _validated_frontier(
        self, session_id: str, epoch: int, cursor_seq: int
    ) -> int | None:
        """验证当前 epoch 的窗口区间连续且没有重叠或缺口。"""
        cursor = await self.connection.execute(
            """
            SELECT start_seq,end_seq
            FROM summary_jobs
            WHERE session_id=? AND session_epoch=?
            ORDER BY start_seq ASC,end_seq ASC,created_at ASC,job_id ASC
            """,
            (session_id, epoch),
        )
        frontier = cursor_seq
        for row in await cursor.fetchall():
            start = int(_row(row, "start_seq", 0))
            end = int(_row(row, "end_seq", 1))
            if end <= cursor_seq:
                continue
            if start < cursor_seq or start != frontier or end <= start:
                return None
            frontier = end
        return frontier

    async def plan_and_enqueue_windows(
        self,
        context: SummaryWindowContext,
        observed_end_seq: int,
        *,
        strict: bool = False,
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
                frontier = await self._validated_frontier(
                    context.session_id, epoch, cursor_seq
                )
                if frontier is None or frontier > observed_end_seq:
                    await self._rollback_summary()
                    if strict:
                        raise RuntimeError("summary_startup_planning_failed")
                    return SummaryEnqueueResult(
                        False, reason_code=SummaryReasonCode.SOURCE_INCOMPLETE
                    )
                start = max(frontier, context.start_seq)
                queued = 0
                duplicates = 0
                blocked = False
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
                        blocked = True
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
                    bool(queued or duplicates or blocked),
                    queued=queued,
                    duplicates=duplicates,
                    active_parallelism=active,
                    target_parallelism=executable,
                    reason_code=(
                        SummaryReasonCode.QUEUED
                        if queued
                        else SummaryReasonCode.DUPLICATE
                        if duplicates
                        else SummaryReasonCode.SOURCE_INCOMPLETE
                        if blocked
                        else SummaryReasonCode.NO_WINDOW
                    ),
                )
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            if strict:
                raise RuntimeError("summary_startup_planning_failed") from error
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


__all__ = ["SummaryStoreMixin"]
