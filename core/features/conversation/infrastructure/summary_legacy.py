"""旧 pending_summary 到总结任务的启动期迁移。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

from ....shared.contracts.conversation import Message
from ...reflection.domain.summary_models import source_window_digest


class SummaryLegacyMigrationMixin:
    """把旧会话元数据中的待重试窗口转换为持久任务。"""

    connection: Any
    _write_lock: Any

    def _summary_now(self) -> float:
        """由组合 Store 提供统一总结时钟。"""
        ...

    async def _begin_summary(self) -> None:
        """由组合 Store 提供总结事务入口。"""
        ...

    async def _rollback_summary(self) -> None:
        """由组合 Store 提供总结事务回滚入口。"""
        ...

    async def _ensure_epoch(self, session_id: str, now: float) -> tuple[int, int]:
        """由组合 Store 提供 epoch 与 cursor 读取入口。"""
        ...

    async def _source_rows(
        self, session_id: str, start_seq: int, end_seq: int
    ) -> Sequence[Any]:
        """由组合 Store 提供稳定 message_seq 来源读取入口。"""
        ...

    @staticmethod
    def _legacy_row_value(row: Any, name: str, index: int) -> Any:
        """兼容 SQLite Row 与测试替身的列读取。"""
        try:
            return row[name]
        except (KeyError, IndexError, TypeError):
            return row[index]

    @staticmethod
    def _legacy_index(value: object) -> int | None:
        """只接受旧游标中的真实非布尔整数。"""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    async def recover_legacy_pending(self) -> int:
        """幂等迁移旧 pending；范围或来源不完整时保留 blocked 保护。"""
        if self.connection is None:
            return 0
        converted = 0
        try:
            async with self._write_lock:
                await self._begin_summary()
                cursor = await self.connection.execute(
                    "SELECT session_id, metadata FROM sessions WHERE metadata IS NOT NULL"
                )
                rows = await cursor.fetchall()
                now = self._summary_now()
                for row in rows:
                    session_id = str(self._legacy_row_value(row, "session_id", 0))
                    try:
                        metadata = json.loads(
                            self._legacy_row_value(row, "metadata", 1) or "{}"
                        )
                    except (TypeError, ValueError):
                        # 无法解析 metadata 时没有安全方式定位 pending，原样保留。
                        continue
                    if (
                        not isinstance(metadata, dict)
                        or "pending_summary" not in metadata
                    ):
                        continue

                    epoch, cursor_seq = await self._ensure_epoch(session_id, now)
                    pending = metadata.get("pending_summary")
                    pending = pending if isinstance(pending, dict) else {}
                    raw_start = self._legacy_index(pending.get("start_index"))
                    raw_end = self._legacy_index(pending.get("end_index"))
                    range_valid = (
                        raw_start is not None
                        and raw_end is not None
                        and raw_end > raw_start
                        and raw_end - raw_start >= 2
                    )
                    start = raw_start if raw_start is not None else cursor_seq
                    end = raw_end if raw_end is not None and raw_end > start else None
                    if end is None:
                        end_cursor = await self.connection.execute(
                            "SELECT COALESCE(MAX(message_seq), 0) FROM messages WHERE session_id=?",
                            (session_id,),
                        )
                        end_row = await end_cursor.fetchone()
                        end = max(start + 1, int(end_row[0] or 0) if end_row else 0)

                    existing = await self.connection.execute(
                        "SELECT 1 FROM summary_jobs WHERE session_id=? AND session_epoch=? AND start_seq=? AND end_seq=?",
                        (session_id, epoch, start, end),
                    )
                    if await existing.fetchone() is not None:
                        metadata.pop("pending_summary", None)
                        await self.connection.execute(
                            "UPDATE sessions SET metadata=? WHERE session_id=?",
                            (json.dumps(metadata, ensure_ascii=False), session_id),
                        )
                        converted += 1
                        continue

                    overlap = await self.connection.execute(
                        """
                        SELECT 1 FROM summary_jobs
                        WHERE session_id=? AND session_epoch=? AND start_seq<? AND end_seq>?
                        LIMIT 1
                        """,
                        (session_id, epoch, end, start),
                    )
                    if await overlap.fetchone() is not None:
                        # 不覆盖已有不同范围；保留旧 pending 等待人工/启动处理。
                        continue

                    source_rows = await self._source_rows(session_id, start, end)
                    complete = False
                    digest = ""
                    gate_snapshot_json = pending.get("gate_snapshot_json")
                    snapshot_available = False
                    if isinstance(gate_snapshot_json, str):
                        try:
                            parsed_snapshot = json.loads(gate_snapshot_json)
                        except (TypeError, ValueError):
                            parsed_snapshot = None
                        snapshot_available = isinstance(parsed_snapshot, dict) and bool(
                            parsed_snapshot
                        )
                    try:
                        messages = tuple(
                            Message.from_dict(
                                {
                                    "id": self._legacy_row_value(item, "id", 0),
                                    "session_id": self._legacy_row_value(
                                        item, "session_id", 1
                                    ),
                                    "role": self._legacy_row_value(item, "role", 2),
                                    "content": self._legacy_row_value(
                                        item, "content", 3
                                    ),
                                    "sender_id": self._legacy_row_value(
                                        item, "sender_id", 4
                                    ),
                                    "sender_name": self._legacy_row_value(
                                        item, "sender_name", 5
                                    ),
                                    "group_id": self._legacy_row_value(
                                        item, "group_id", 6
                                    ),
                                    "platform": self._legacy_row_value(
                                        item, "platform", 7
                                    ),
                                    "timestamp": self._legacy_row_value(
                                        item, "timestamp", 8
                                    ),
                                    "metadata": self._legacy_row_value(
                                        item, "metadata", 9
                                    ),
                                }
                            )
                            for item in source_rows
                        )
                        seqs = tuple(
                            int(self._legacy_row_value(item, "message_seq", 10))
                            for item in source_rows
                        )
                        complete = (
                            range_valid
                            and snapshot_available
                            and len(messages) == end - start
                            and seqs == tuple(range(start + 1, end + 1))
                        )
                        if complete:
                            digest = source_window_digest(messages, seqs)
                    except (KeyError, TypeError, ValueError):
                        complete = False
                    if not digest:
                        digest = hashlib.sha256(
                            f"incomplete:{session_id}:{start}:{end}".encode()
                        ).hexdigest()
                    status = "queued" if complete else "blocked"
                    reason = "legacy_pending" if complete else "legacy_pending_invalid"
                    await self.connection.execute(
                        """
                        INSERT INTO summary_jobs(
                          job_id,session_id,session_epoch,start_seq,end_seq,expected_count,
                          source_digest,gate_snapshot_json,triggered_by,status,attempt_count,
                          next_attempt_at,worker_generation,reason_code,created_at,updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uuid.uuid4().hex,
                            session_id,
                            epoch,
                            start,
                            end,
                            end - start,
                            digest,
                            gate_snapshot_json if snapshot_available else "{}",
                            "legacy",
                            status,
                            0,
                            now,
                            0,
                            reason,
                            now,
                            now,
                        ),
                    )
                    metadata.pop("pending_summary", None)
                    await self.connection.execute(
                        "UPDATE sessions SET metadata=? WHERE session_id=?",
                        (json.dumps(metadata, ensure_ascii=False), session_id),
                    )
                    converted += 1
                await self.connection.commit()
                return converted
        except asyncio.CancelledError:
            await self._rollback_summary()
            raise
        except Exception as error:
            await self._rollback_summary()
            raise RuntimeError("summary_recovery_failed") from error

    async def migrate_legacy_pending(self) -> int:
        """兼容命名入口，委托启动期 pending 迁移。"""
        return await self.recover_legacy_pending()


__all__ = ["SummaryLegacyMigrationMixin"]
