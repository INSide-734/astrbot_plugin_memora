"""Topic catalog 状态、generation 和 dirty queue 的存储操作。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ..application.write_coordinator import coordinated_transaction
from .topic_catalog_models import TopicCatalogDirty

_ALLOWED_OPERATIONS = frozenset({"add", "metadata_update", "status_update", "delete"})
_ALLOWED_STATES = frozenset({"pending", "running", "completed", "failed"})
_ALLOWED_REASON_CODES = frozenset({"canonical_changed", "catalog_repair_failed"})


class TopicCatalogStateMixin:
    """提供目录状态、generation fencing 和 dirty lease 操作。"""

    _db: Any | None = None

    async def replace_memory_mappings(
        self,
        memory_id: int,
        generation: int,
        *,
        owner_token: str | None = None,
        now: float | None = None,
    ) -> bool:
        """由具体 Store 重写的 mapping 替换端口。"""

        raise NotImplementedError

    @staticmethod
    def _empty_state(reason_code: str) -> dict[str, Any]:
        """返回不含敏感标识的空目录状态。"""

        return {
            "status": "empty",
            "active_generation": None,
            "staging_generation": None,
            "canonical_snapshot_revision": None,
            "canonical_write_watermark": 0,
            "published_dirty_watermark": 0,
            "next_dirty_sequence": 0,
            "backfill_cursor": 0,
            "backfill_total": 0,
            "staging_start_watermark": 0,
            "rebuild_lease_until": None,
            "updated_at": 0.0,
            "reason_code": reason_code,
        }

    async def get_state(self) -> dict[str, Any]:
        """读取固定 allowlist 的 catalog 状态标量。"""

        if self._db is None:
            return self._empty_state("catalog_unavailable")
        cursor = await self._db.execute(
            """
            SELECT status, active_generation, staging_generation, backfill_cursor,
                   backfill_total, staging_start_watermark,
                   canonical_snapshot_revision, canonical_write_watermark,
                   published_dirty_watermark, next_dirty_sequence,
                   rebuild_lease_until, updated_at, reason_code
            FROM topic_catalog_state WHERE id = 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return self._empty_state("catalog_empty")
        return {
            "status": str(row[0]),
            "active_generation": row[1],
            "staging_generation": row[2],
            "backfill_cursor": int(row[3] or 0),
            "backfill_total": int(row[4] or 0),
            "staging_start_watermark": int(row[5] or 0),
            "canonical_snapshot_revision": row[6],
            "canonical_write_watermark": int(row[7] or 0),
            "published_dirty_watermark": int(row[8] or 0),
            "next_dirty_sequence": int(row[9] or 0),
            "rebuild_lease_until": row[10],
            "updated_at": float(row[11] or 0),
            "reason_code": str(row[12]),
        }

    async def register_dirty(
        self,
        memory_id: int,
        operation: str,
        *,
        reason_code: str = "canonical_changed",
    ) -> int:
        """在 canonical 写事务中登记只含 memory ID 的 dirty。"""

        self._validate_dirty_input(memory_id, operation, reason_code)
        if self._db is None:
            raise RuntimeError("TopicCatalogStore 尚未绑定数据库连接")
        now = max(0.0, time.time())
        async with coordinated_transaction(self._db) as db:
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET canonical_write_watermark = canonical_write_watermark + 1,
                    next_dirty_sequence = next_dirty_sequence + 1,
                    updated_at = ?, reason_code = ?
                WHERE id = 1
                """,
                (now, reason_code),
            )
            if result.rowcount != 1:
                raise RuntimeError("catalog_state_missing")
            row = await (
                await db.execute(
                    "SELECT next_dirty_sequence FROM topic_catalog_state WHERE id = 1"
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("catalog_state_missing")
            sequence = int(row[0])
            await db.execute(
                """
                INSERT INTO topic_catalog_dirty(
                    memory_id, operation, sequence, state, attempt_count,
                    last_error_code, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    operation = excluded.operation,
                    sequence = excluded.sequence,
                    state = CASE
                        WHEN topic_catalog_dirty.state = 'running' THEN 'running'
                        ELSE 'pending'
                    END,
                    attempt_count = CASE
                        WHEN topic_catalog_dirty.state = 'running'
                        THEN topic_catalog_dirty.attempt_count
                        ELSE 0
                    END,
                    lease_until = CASE
                        WHEN topic_catalog_dirty.state = 'running'
                        THEN topic_catalog_dirty.lease_until
                        ELSE NULL
                    END,
                    lease_owner_token = CASE
                        WHEN topic_catalog_dirty.state = 'running'
                        THEN topic_catalog_dirty.lease_owner_token
                        ELSE NULL
                    END,
                    last_error_code = excluded.last_error_code,
                    updated_at = excluded.updated_at
                """,
                (memory_id, operation, sequence, reason_code, now, now),
            )
        return sequence

    async def begin_generation(
        self,
        generation: int,
        owner_token: str,
        lease_until: float,
        *,
        backfill_total: int | None = None,
        now: float | None = None,
    ) -> bool:
        """以 lease CAS 开始或恢复 staging generation。"""
        current_time = max(0.0, time.time() if now is None else now)
        if (
            generation <= 0
            or not owner_token
            or lease_until <= current_time
            or (backfill_total is not None and backfill_total < 0)
        ):
            return False
        if self._db is None:
            return False
        async with coordinated_transaction(self._db) as db:
            row = await (
                await db.execute(
                    """
                    SELECT rebuild_owner_token, rebuild_lease_until,
                           staging_generation, active_generation, backfill_cursor,
                           backfill_total, canonical_write_watermark,
                           staging_start_watermark, status
                    FROM topic_catalog_state WHERE id = 1
                    """
                )
            ).fetchone()
            if row is None:
                return False
            current_owner, current_lease = row[0], row[1]
            staging_generation, active_generation = row[2], row[3]
            if (
                current_owner
                and current_owner != owner_token
                and current_lease is not None
                and float(current_lease) > current_time
            ):
                return False
            if active_generation is not None and generation <= int(active_generation):
                return False
            # watermark 前移则不 resuming：重置 staging 从头回填；
            # livelock 经 reason_code=catalog_backfill_reset 观测。
            resuming = staging_generation == generation and int(row[6] or 0) == int(
                row[7] or 0
            )
            if not resuming:
                if staging_generation is not None:
                    await db.execute(
                        "DELETE FROM memory_topic_sources WHERE generation = ?",
                        (staging_generation,),
                    )
                    await db.execute(
                        "DELETE FROM scope_topics WHERE generation = ?",
                        (staging_generation,),
                    )
                await db.execute(
                    "DELETE FROM memory_topic_sources WHERE generation = ?",
                    (generation,),
                )
                await db.execute(
                    "DELETE FROM scope_topics WHERE generation = ?",
                    (generation,),
                )
                cursor = 0
                total = 0 if backfill_total is None else backfill_total
                start_watermark = int(row[6] or 0)
            else:
                cursor = int(row[4] or 0)
                total = int(row[5] or 0) if backfill_total is None else backfill_total
                start_watermark = int(row[7] or 0)
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET staging_generation = ?,
                    status = CASE WHEN active_generation IS NULL
                                  THEN 'backfilling'
                                  WHEN status = 'degraded' THEN 'degraded'
                                  ELSE 'ready' END,
                    backfill_cursor = ?, backfill_total = ?,
                    staging_start_watermark = ?,
                    rebuild_owner_token = ?, rebuild_lease_until = ?,
                    updated_at = ?, reason_code = ?
                WHERE id = 1
                """,
                (
                    generation,
                    cursor,
                    total,
                    start_watermark,
                    owner_token,
                    lease_until,
                    current_time,
                    # resuming=False 表示 staging 被重置（写入竞争），
                    # 用独立 reason code 暴露 livelock 观测信号
                    "catalog_backfill_resumed"
                    if resuming
                    else "catalog_backfill_reset",
                ),
            )
            return result.rowcount == 1

    async def renew_generation_lease(
        self,
        generation: int,
        owner_token: str,
        lease_until: float,
        *,
        now: float | None = None,
    ) -> bool:
        """仅由当前未过期 owner 续租 staging generation。"""
        current_time = max(0.0, time.time() if now is None else now)
        if generation <= 0 or not owner_token or lease_until <= current_time:
            return False
        if self._db is None:
            return False
        async with coordinated_transaction(self._db) as db:
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET rebuild_lease_until = ?, updated_at = ?
                WHERE id = 1 AND staging_generation = ?
                  AND rebuild_owner_token = ? AND rebuild_lease_until > ?
                """,
                (lease_until, current_time, generation, owner_token, current_time),
            )
            return result.rowcount == 1

    async def advance_backfill_cursor(
        self,
        generation: int,
        owner_token: str,
        cursor: int,
        *,
        now: float | None = None,
    ) -> bool:
        """以 owner/lease CAS 单调推进 keyset cursor（最后处理的 documents.id）。

        cursor 是 id 水位而非行数；完成判定由 rebuild 空批次后走 publish fence。
        """
        current_time = max(0.0, time.time() if now is None else now)
        if self._db is None or generation <= 0 or not owner_token or cursor < 0:
            return False
        async with coordinated_transaction(self._db) as db:
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET backfill_cursor = ?, updated_at = ?
                WHERE id = 1 AND staging_generation = ?
                  AND rebuild_owner_token = ? AND rebuild_lease_until > ?
                  AND backfill_cursor <= ?
                """,
                (
                    cursor,
                    current_time,
                    generation,
                    owner_token,
                    current_time,
                    cursor,
                ),
            )
            return result.rowcount == 1

    async def mark_dirty_reconciled(
        self,
        owner_token: str,
        *,
        up_to_sequence: int,
        covered_memory_ids: set[int] | frozenset[int] | None = None,
        now: float | None = None,
    ) -> bool:
        """仅在 staging 已覆盖全部待收敛 source 时收敛历史 dirty。"""

        if self._db is None or not owner_token or up_to_sequence < 0:
            return False
        covered = set(covered_memory_ids or ())
        if any(
            not isinstance(memory_id, int)
            or isinstance(memory_id, bool)
            or memory_id <= 0
            for memory_id in covered
        ):
            return False
        current_time = max(0.0, time.time() if now is None else now)
        async with coordinated_transaction(self._db) as db:
            owner = await (
                await db.execute(
                    """
                    SELECT rebuild_owner_token, rebuild_lease_until
                    FROM topic_catalog_state WHERE id = 1
                    """
                )
            ).fetchone()
            if (
                owner is None
                or owner[0] != owner_token
                or owner[1] is None
                or float(owner[1]) <= current_time
            ):
                return False
            running = await (
                await db.execute(
                    """
                    SELECT 1 FROM topic_catalog_dirty
                    WHERE sequence <= ? AND state = 'running'
                      AND lease_until IS NOT NULL AND lease_until > ?
                      AND (lease_owner_token IS NULL OR lease_owner_token != ?)
                    LIMIT 1
                    """,
                    (up_to_sequence, current_time, owner_token),
                )
            ).fetchone()
            if running is not None:
                return False
            dirty_cursor = await db.execute(
                """
                SELECT memory_id FROM topic_catalog_dirty
                WHERE sequence <= ? AND (
                    state IN ('pending', 'failed')
                    OR (state = 'running' AND (
                        lease_until IS NULL OR lease_until <= ?
                    ))
                )
                """,
                (up_to_sequence, current_time),
            )
            if any(int(row[0]) not in covered for row in await dirty_cursor.fetchall()):
                return False
            result = await db.execute(
                """
                UPDATE topic_catalog_dirty
                SET state = 'completed', lease_owner_token = NULL,
                    lease_until = NULL, last_error_code = NULL, updated_at = ?
                WHERE sequence <= ? AND (
                    state IN ('pending', 'failed')
                    OR (state = 'running' AND (lease_until IS NULL OR lease_until <= ?))
                )
                """,
                (current_time, up_to_sequence, current_time),
            )
            return result.rowcount >= 0

    async def abandon_generation(
        self,
        generation: int,
        owner_token: str,
        *,
        reason_code: str = "catalog_rebuild_failed",
        now: float | None = None,
    ) -> bool:
        """放弃未发布 staging，保留旧 active generation。"""

        if self._db is None or generation <= 0 or not owner_token:
            return False
        current_time = max(0.0, time.time() if now is None else now)
        async with coordinated_transaction(self._db) as db:
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET staging_generation = NULL,
                    status = CASE WHEN active_generation IS NULL
                                   THEN 'degraded' ELSE status END,
                    rebuild_owner_token = NULL, rebuild_lease_until = NULL,
                    updated_at = ?, reason_code = ?
                WHERE id = 1 AND staging_generation = ?
                  AND rebuild_owner_token = ?
                  AND (active_generation IS NULL OR active_generation != ?)
                """,
                (current_time, reason_code, generation, owner_token, generation),
            )
            if result.rowcount != 1:
                return False
            await db.execute(
                "DELETE FROM memory_topic_sources WHERE generation = ?",
                (generation,),
            )
            await db.execute(
                "DELETE FROM scope_topics WHERE generation = ?",
                (generation,),
            )
            return True

    async def publish_generation(
        self,
        generation: int,
        owner_token: str,
        *,
        start_watermark: int,
        published_dirty_watermark: int,
        canonical_snapshot_revision: str,
        expected_source_count: int | None = None,
        expected_mapping_count: int | None = None,
        now: float | None = None,
    ) -> bool:
        """在 owner、watermark、dirty 和 staging 数量均稳定时发布。"""

        if (
            generation <= 0
            or not owner_token
            or start_watermark < 0
            or published_dirty_watermark < 0
            or not isinstance(canonical_snapshot_revision, str)
            or not canonical_snapshot_revision.strip()
            or self._db is None
        ):
            return False
        if (
            expected_source_count is None
            or expected_mapping_count is None
            or expected_source_count < 0
            or expected_mapping_count < 0
        ):
            return False
        current_time = max(0.0, time.time() if now is None else now)
        async with coordinated_transaction(self._db) as db:
            state = await (
                await db.execute(
                    """
                    SELECT active_generation, staging_generation, status,
                           rebuild_owner_token, rebuild_lease_until,
                           canonical_write_watermark, staging_start_watermark,
                           backfill_cursor, backfill_total
                    FROM topic_catalog_state WHERE id = 1
                    """
                )
            ).fetchone()
            if (
                state is None
                or state[1] != generation
                or state[2] not in {"backfilling", "ready", "degraded"}
                or state[3] != owner_token
                or state[4] is None
                or float(state[4]) <= current_time
                or int(state[5]) != start_watermark
                or int(state[6]) != start_watermark
                or published_dirty_watermark != start_watermark
            ):
                return False
            # keyset 语义：cursor 是最后处理的 documents.id 水位，
            # 回填完成由 rebuild 循环取空批次保证；此处只验证 cursor
            # 不小于全部 documents 的最大 id。
            backfill_cursor = int(state[7] or 0)
            max_document_id = await (
                await db.execute("SELECT COALESCE(MAX(id), 0) FROM documents")
            ).fetchone()
            if backfill_cursor < int(max_document_id[0] or 0):
                return False
            dirty = await (
                await db.execute(
                    """
                    SELECT 1 FROM topic_catalog_dirty
                    WHERE sequence <= ? AND state != 'completed' LIMIT 1
                    """,
                    (published_dirty_watermark,),
                )
            ).fetchone()
            if dirty is not None:
                return False
            source_count, mapping_count = await (
                await db.execute(
                    """
                    SELECT COUNT(DISTINCT memory_id), COUNT(*)
                    FROM memory_topic_sources WHERE generation = ?
                    """,
                    (generation,),
                )
            ).fetchone()
            if int(source_count or 0) != expected_source_count:
                return False
            if int(mapping_count or 0) != expected_mapping_count:
                return False
            aggregate_mismatch = await (
                await db.execute(
                    """
                    WITH mapping_counts AS (
                        SELECT scope_key, chat_type, privacy_level, topic_key,
                               COUNT(*) AS source_count
                        FROM memory_topic_sources WHERE generation = ?
                        GROUP BY scope_key, chat_type, privacy_level, topic_key
                    ), aggregate_counts AS (
                        SELECT scope_key, chat_type, privacy_level, topic_key,
                               active_source_count
                        FROM scope_topics WHERE generation = ?
                    )
                    SELECT 1 FROM mapping_counts AS mapping
                    LEFT JOIN aggregate_counts AS aggregate
                      ON aggregate.scope_key = mapping.scope_key
                     AND aggregate.chat_type = mapping.chat_type
                     AND aggregate.privacy_level = mapping.privacy_level
                     AND aggregate.topic_key = mapping.topic_key
                    WHERE aggregate.topic_key IS NULL
                       OR mapping.source_count != aggregate.active_source_count
                    UNION ALL
                    SELECT 1 FROM aggregate_counts AS aggregate
                    LEFT JOIN mapping_counts AS mapping
                      ON mapping.scope_key = aggregate.scope_key
                     AND mapping.chat_type = aggregate.chat_type
                     AND mapping.privacy_level = aggregate.privacy_level
                     AND mapping.topic_key = aggregate.topic_key
                    WHERE mapping.topic_key IS NULL
                       OR mapping.source_count != aggregate.active_source_count
                    LIMIT 1
                    """,
                    (generation, generation),
                )
            ).fetchone()
            if aggregate_mismatch is not None:
                return False
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET active_generation = ?, staging_generation = NULL, status = 'ready',
                    canonical_snapshot_revision = ?,
                    published_dirty_watermark = ?,
                    rebuild_owner_token = NULL, rebuild_lease_until = NULL,
                    updated_at = ?, reason_code = 'catalog_ready'
                WHERE id = 1 AND staging_generation = ?
                  AND rebuild_owner_token = ? AND rebuild_lease_until > ?
                  AND canonical_write_watermark = ?
                """,
                (
                    generation,
                    canonical_snapshot_revision.strip(),
                    published_dirty_watermark,
                    current_time,
                    generation,
                    owner_token,
                    current_time,
                    start_watermark,
                ),
            )
            return result.rowcount == 1

    async def readable_generation(self) -> int | None:
        """返回满足 ready 条件的 active generation，否则返回空。"""

        state = await self.get_state()
        generation = state.get("active_generation")
        return (
            int(generation) if state.get("status") == "ready" and generation else None
        )

    async def claim_dirty(
        self,
        owner_token: str,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
        limit: int = 25,
    ) -> tuple[TopicCatalogDirty, ...]:
        """以 owner/lease CAS 领取待处理 dirty。"""

        if self._db is None or not owner_token or lease_seconds <= 0 or limit <= 0:
            return ()
        current_time = max(0.0, time.time() if now is None else now)
        lease_until = current_time + lease_seconds
        async with coordinated_transaction(self._db) as db:
            await db.execute(
                """
                UPDATE topic_catalog_dirty
                SET state = 'pending', lease_owner_token = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE state = 'running' AND lease_until IS NOT NULL AND lease_until <= ?
                """,
                (current_time, current_time),
            )
            cursor = await db.execute(
                """
                SELECT dirty_id FROM topic_catalog_dirty
                WHERE state IN ('pending','failed')
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY sequence ASC, dirty_id ASC LIMIT ?
                """,
                (current_time, min(limit, 100)),
            )
            dirty_ids = [int(row[0]) for row in await cursor.fetchall()]
            if dirty_ids:
                placeholders = ",".join("?" for _ in dirty_ids)
                await db.execute(
                    f"""
                    UPDATE topic_catalog_dirty
                    SET state = 'running', lease_owner_token = ?, lease_until = ?,
                        attempt_count = attempt_count + 1, updated_at = ?
                    WHERE dirty_id IN ({placeholders})
                      AND state IN ('pending','failed')
                    """,
                    (owner_token, lease_until, current_time, *dirty_ids),
                )
        return await self.list_dirty(
            states=("running",), limit=limit, owner_token=owner_token
        )

    async def finish_dirty(
        self,
        dirty_id: int,
        *,
        owner_token: str,
        sequence: int,
        success: bool,
        reason_code: str | None = None,
        now: float | None = None,
    ) -> bool:
        """以 owner、lease、sequence 和状态 CAS 收敛 dirty。"""

        if self._db is None or dirty_id <= 0 or not owner_token or sequence <= 0:
            return False
        if reason_code is not None and reason_code not in _ALLOWED_REASON_CODES:
            return False
        current_time = max(0.0, time.time() if now is None else now)
        state = "completed" if success else "failed"
        async with coordinated_transaction(self._db) as db:
            result = await db.execute(
                """
                UPDATE topic_catalog_dirty
                SET state = ?, lease_owner_token = NULL, lease_until = NULL,
                    last_error_code = ?, updated_at = ?
                WHERE dirty_id = ? AND state = 'running'
                  AND lease_owner_token = ? AND lease_until > ? AND sequence = ?
                """,
                (
                    state,
                    reason_code,
                    current_time,
                    dirty_id,
                    owner_token,
                    current_time,
                    sequence,
                ),
            )
            return result.rowcount == 1

    async def list_dirty(
        self,
        *,
        states: tuple[str, ...] = ("pending", "failed"),
        limit: int = 100,
        owner_token: str | None = None,
    ) -> tuple[TopicCatalogDirty, ...]:
        """读取按 sequence 排序且可按 owner 隔离的 dirty 投影。"""

        if self._db is None or not states or limit <= 0:
            return ()
        safe_states = tuple(state for state in states if state in _ALLOWED_STATES)
        if not safe_states:
            return ()
        placeholders = ",".join("?" for _ in safe_states)
        owner_clause = ""
        params: list[Any] = [*safe_states]
        if owner_token is not None:
            owner_clause = " AND lease_owner_token = ?"
            params.append(owner_token)
        params.append(min(limit, 1000))
        cursor = await self._db.execute(
            f"""
            SELECT dirty_id, memory_id, operation, sequence, state,
                   attempt_count, lease_until, last_error_code
            FROM topic_catalog_dirty
            WHERE state IN ({placeholders}){owner_clause}
            ORDER BY sequence ASC, dirty_id ASC
            LIMIT ?
            """,
            params,
        )
        return tuple(
            TopicCatalogDirty(
                dirty_id=int(row[0]),
                memory_id=int(row[1]),
                operation=str(row[2]),
                sequence=int(row[3]),
                state=str(row[4]),
                attempt_count=int(row[5] or 0),
                lease_until=(float(row[6]) if row[6] is not None else None),
                last_error_code=(str(row[7]) if row[7] is not None else None),
            )
            for row in await cursor.fetchall()
        )

    async def repair_pending(self, owner_token: str, *, limit: int = 25) -> int:
        """领取 dirty 并按当前 active generation 修复。"""

        if not owner_token:
            return 0
        repaired_count = 0
        for dirty in await self.claim_dirty(owner_token, limit=limit):
            try:
                state = await self.get_state()
                generation = state.get("active_generation")
                repaired = bool(
                    generation
                    and await self.replace_memory_mappings(
                        dirty.memory_id,
                        int(generation),
                    )
                )
                finished = await self.finish_dirty(
                    dirty.dirty_id,
                    owner_token=owner_token,
                    sequence=dirty.sequence,
                    success=repaired,
                    reason_code=None if repaired else "catalog_repair_failed",
                )
                repaired_count += int(repaired and finished)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("话题目录 dirty 修复失败", exc_info=True)
                await self.finish_dirty(
                    dirty.dirty_id,
                    owner_token=owner_token,
                    sequence=dirty.sequence,
                    success=False,
                    reason_code="catalog_repair_failed",
                )
        return repaired_count

    @staticmethod
    def _validate_dirty_input(
        memory_id: int,
        operation: str,
        reason_code: str,
    ) -> None:
        """验证 dirty 登记的固定边界，拒绝携带快照的输入。"""

        if (
            not isinstance(memory_id, int)
            or isinstance(memory_id, bool)
            or memory_id <= 0
        ):
            raise ValueError("catalog_memory_id_invalid")
        if operation not in _ALLOWED_OPERATIONS:
            raise ValueError("catalog_operation_invalid")
        if reason_code not in _ALLOWED_REASON_CODES:
            raise ValueError("catalog_reason_invalid")


__all__ = ["TopicCatalogStateMixin"]
