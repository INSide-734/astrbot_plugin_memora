"""Topic catalog generation 回填、发布校验和失败恢复。"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from astrbot.api import logger

from .topic_catalog_state import TopicCatalogStateMixin


class TopicCatalogRebuildMixin(TopicCatalogStateMixin):
    """提供 topic catalog 的 generation 回填与发布生命周期。"""

    def _catalog_transaction(self) -> Any:
        """返回由具体 Store 提供的 catalog 写事务上下文。"""

        raise NotImplementedError

    async def generation_counts(self, generation: int) -> tuple[int, int] | None:
        """返回 generation 的 source 与 mapping 数量。"""

        if self._db is None or generation <= 0:
            return None
        row = await (
            await self._db.execute(
                "SELECT COUNT(DISTINCT memory_id), COUNT(*) FROM memory_topic_sources WHERE generation = ?",
                (generation,),
            )
        ).fetchone()
        return None if row is None else (int(row[0] or 0), int(row[1] or 0))

    async def retire_generation(self, generation: int) -> bool:
        """在新 generation 复核成功后删除不再活跃的旧派生行。"""

        if self._db is None or generation <= 0:
            return False
        async with self._catalog_transaction() as db:
            state = await (
                await db.execute(
                    """
                    SELECT active_generation, staging_generation
                    FROM topic_catalog_state WHERE id = 1
                    """
                )
            ).fetchone()
            if state is None or state[0] == generation or state[1] == generation:
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

    async def has_orphan_generations(self) -> bool:
        """判断是否存在既非 active 也非 staging 的派生 generation。"""

        if self._db is None:
            return False
        row = await (
            await self._db.execute(
                """
                SELECT 1 FROM memory_topic_sources
                WHERE generation != COALESCE(
                    (SELECT active_generation FROM topic_catalog_state WHERE id = 1), 0
                )
                  AND generation != COALESCE(
                    (SELECT staging_generation FROM topic_catalog_state WHERE id = 1), 0
                )
                UNION ALL
                SELECT 1 FROM scope_topics
                WHERE generation != COALESCE(
                    (SELECT active_generation FROM topic_catalog_state WHERE id = 1), 0
                )
                  AND generation != COALESCE(
                    (SELECT staging_generation FROM topic_catalog_state WHERE id = 1), 0
                )
                LIMIT 1
                """
            )
        ).fetchone()
        return row is not None

    async def cleanup_orphan_generations(self) -> bool:
        """事务性清理孤儿派生 generation，保留 active 与 staging。"""

        if self._db is None:
            return False
        async with self._catalog_transaction() as db:
            state = await (
                await db.execute(
                    """
                    SELECT active_generation, staging_generation
                    FROM topic_catalog_state WHERE id = 1
                    """
                )
            ).fetchone()
            if state is None:
                return False
            active_generation = int(state[0] or 0)
            staging_generation = int(state[1] or 0)
            await db.execute(
                """
                DELETE FROM memory_topic_sources
                WHERE generation != ? AND generation != ?
                """,
                (active_generation, staging_generation),
            )
            await db.execute(
                """
                DELETE FROM scope_topics
                WHERE generation != ? AND generation != ?
                """,
                (active_generation, staging_generation),
            )
            return True

    async def verify_published_generation(self, generation: int) -> bool:
        """校验已发布 generation 的 mapping 与 scope 聚合数量一致。"""

        if self._db is None or generation <= 0:
            return False
        state = await (
            await self._db.execute(
                "SELECT status, active_generation FROM topic_catalog_state WHERE id = 1"
            )
        ).fetchone()
        if state is None or state[0] != "ready" or state[1] != generation:
            return False
        mismatch = await (
            await self._db.execute(
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
        return mismatch is None

    async def mark_degraded(
        self,
        reason_code: str,
        *,
        now: float | None = None,
    ) -> bool:
        """原子降级并只清理当前记录的 staging generation。"""

        if self._db is None or not reason_code or len(reason_code) > 128:
            return False
        current_time = max(0.0, time.time() if now is None else now)
        async with self._catalog_transaction() as db:
            state = await (
                await db.execute(
                    """
                    SELECT active_generation, staging_generation
                    FROM topic_catalog_state WHERE id = 1
                    """
                )
            ).fetchone()
            if state is None:
                return False
            active_generation, staging_generation = state
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET status = 'degraded', staging_generation = NULL,
                    rebuild_owner_token = NULL, rebuild_lease_until = NULL,
                    updated_at = ?, reason_code = ?
                WHERE id = 1
                """,
                (current_time, reason_code),
            )
            if result.rowcount != 1:
                return False
            if staging_generation not in (None, active_generation):
                await db.execute(
                    "DELETE FROM memory_topic_sources WHERE generation = ?",
                    (staging_generation,),
                )
                await db.execute(
                    "DELETE FROM scope_topics WHERE generation = ?",
                    (staging_generation,),
                )
            return True

    async def restore_generation_after_verify_failure(
        self,
        failed_generation: int,
        *,
        previous_generation: int | None,
        previous_published_dirty_watermark: int,
        previous_canonical_snapshot_revision: str | None,
        reason_code: str,
        now: float | None = None,
    ) -> bool:
        """原子撤回未通过复核的已发布 generation 并清理其派生行。"""

        if (
            self._db is None
            or failed_generation <= 0
            or previous_published_dirty_watermark < 0
            or not reason_code
            or len(reason_code) > 128
        ):
            return False
        if previous_generation is not None and (
            previous_generation <= 0 or previous_generation == failed_generation
        ):
            return False
        snapshot_revision = (
            previous_canonical_snapshot_revision.strip() or None
            if isinstance(previous_canonical_snapshot_revision, str)
            else None
        )
        current_time = max(0.0, time.time() if now is None else now)
        async with self._catalog_transaction() as db:
            result = await db.execute(
                """
                UPDATE topic_catalog_state
                SET active_generation = ?, staging_generation = NULL,
                    status = CASE WHEN ? IS NULL THEN 'degraded' ELSE 'ready' END,
                    canonical_snapshot_revision = ?,
                    published_dirty_watermark = ?,
                    rebuild_owner_token = NULL, rebuild_lease_until = NULL,
                    updated_at = ?, reason_code = ?
                WHERE id = 1 AND active_generation = ?
                  AND staging_generation IS NULL
                """,
                (
                    previous_generation,
                    previous_generation,
                    snapshot_revision,
                    previous_published_dirty_watermark,
                    current_time,
                    reason_code,
                    failed_generation,
                ),
            )
            if result.rowcount != 1:
                clear_failed = await db.execute(
                    """
                    UPDATE topic_catalog_state
                    SET active_generation = NULL, staging_generation = NULL,
                        status = 'degraded', canonical_snapshot_revision = NULL,
                        rebuild_owner_token = NULL, rebuild_lease_until = NULL,
                        updated_at = ?, reason_code = ?
                    WHERE id = 1 AND active_generation = ?
                      AND staging_generation IS NULL
                    """,
                    (current_time, reason_code, failed_generation),
                )
                if clear_failed.rowcount != 1:
                    return False
            await db.execute(
                "DELETE FROM memory_topic_sources WHERE generation = ?",
                (failed_generation,),
            )
            await db.execute(
                "DELETE FROM scope_topics WHERE generation = ?",
                (failed_generation,),
            )
            return True

    async def rebuild_from_canonical(
        self,
        *,
        batch_size: int = 200,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> dict[str, Any]:
        """从 canonical documents 分批重建 staging generation 并安全发布。"""

        if self._db is None or batch_size <= 0 or lease_seconds <= 0:
            return {"success": False, "reason_code": "catalog_rebuild_invalid"}
        current_time = max(0.0, time.time() if now is None else now)
        state = await self.get_state()
        unresolved = await self.list_dirty(
            states=("pending", "running", "failed"), limit=1
        )
        if (
            state.get("status") == "ready"
            and state.get("active_generation")
            and state.get("staging_generation") is None
            and int(state.get("canonical_write_watermark") or 0)
            == int(state.get("published_dirty_watermark") or 0)
            and not unresolved
            and await self.verify_published_generation(int(state["active_generation"]))
        ):
            return {
                "success": True,
                "status": "skipped",
                "generation": int(state["active_generation"]),
                "reason_code": "catalog_ready",
            }
        staging = state.get("staging_generation")
        generation = int(staging or (int(state.get("active_generation") or 0) + 1))
        owner_token = uuid4().hex
        lease_until = current_time + lease_seconds
        total_cursor = await self._db.execute("SELECT COUNT(*) FROM documents")
        total_row = await total_cursor.fetchone()
        total = int(total_row[0] or 0) if total_row else 0
        if not await self.begin_generation(
            generation,
            owner_token,
            lease_until,
            backfill_total=total,
            now=current_time,
        ):
            return {"success": False, "reason_code": "catalog_rebuild_busy"}
        try:
            verified_total = await (
                await self._db.execute("SELECT COUNT(*) FROM documents")
            ).fetchone()
            if verified_total is None or int(verified_total[0] or 0) != total:
                await self.abandon_generation(
                    generation, owner_token, reason_code="catalog_rebuild_fenced"
                )
                return {"success": False, "reason_code": "catalog_rebuild_fenced"}
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.abandon_generation(
                generation, owner_token, reason_code="catalog_rebuild_failed"
            )
            logger.warning("话题目录回填计数校验失败", exc_info=True)
            return {"success": False, "reason_code": "catalog_rebuild_failed"}

        try:
            while True:
                current_time = max(0.0, time.time() if now is None else now)
                state = await self.get_state()
                cursor = int(state.get("backfill_cursor") or 0)
                # keyset 分页：OFFSET 在回填期间删除 documents 时会跳行；
                # cursor 记录最后处理的 id，WHERE id > cursor 语义稳定。
                rows = await (
                    await self._db.execute(
                        "SELECT id FROM documents WHERE id > ? ORDER BY id LIMIT ?",
                        (cursor, min(batch_size, 1000)),
                    )
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    if not await self.replace_memory_mappings(
                        int(row[0]),
                        generation,
                        owner_token=owner_token,
                        now=max(0.0, time.time() if now is None else now),
                    ):
                        await self.abandon_generation(
                            generation,
                            owner_token,
                            reason_code="catalog_rebuild_failed",
                        )
                        return {
                            "success": False,
                            "reason_code": "catalog_rebuild_failed",
                        }
                if not await self.advance_backfill_cursor(
                    generation,
                    owner_token,
                    int(rows[-1][0]),
                    now=max(0.0, time.time() if now is None else now),
                ):
                    await self.abandon_generation(
                        generation, owner_token, reason_code="catalog_rebuild_fenced"
                    )
                    return {"success": False, "reason_code": "catalog_rebuild_fenced"}
                lease_now = max(0.0, time.time() if now is None else now)
                if not await self.renew_generation_lease(
                    generation,
                    owner_token,
                    lease_now + lease_seconds,
                    now=lease_now,
                ):
                    await self.abandon_generation(
                        generation, owner_token, reason_code="catalog_rebuild_fenced"
                    )
                    return {"success": False, "reason_code": "catalog_rebuild_fenced"}

            state = await self.get_state()
            start_watermark = int(state.get("staging_start_watermark") or 0)
            final_now = max(0.0, time.time() if now is None else now)
            covered_memory_ids = await self._cover_staging_dirty_sources(
                generation,
                owner_token,
                up_to_sequence=start_watermark,
                now=final_now,
            )
            if covered_memory_ids is None or not await self.mark_dirty_reconciled(
                owner_token,
                up_to_sequence=start_watermark,
                covered_memory_ids=covered_memory_ids,
                now=final_now,
            ):
                await self.abandon_generation(
                    generation, owner_token, reason_code="catalog_dirty_unresolved"
                )
                return {"success": False, "reason_code": "catalog_dirty_unresolved"}
            counts = await self.generation_counts(generation)
            if counts is None or not await self.publish_generation(
                generation,
                owner_token,
                start_watermark=start_watermark,
                published_dirty_watermark=start_watermark,
                # canonical_snapshot_revision 实为回填开始时的
                # canonical_write_watermark 快照（documents 无全局 revision），
                # 发布 fencing 完全依赖 watermark，本字段仅作诊断标识。
                canonical_snapshot_revision=str(start_watermark),
                expected_source_count=counts[0],
                expected_mapping_count=counts[1],
                now=max(0.0, time.time() if now is None else now),
            ):
                await self.abandon_generation(
                    generation, owner_token, reason_code="catalog_publish_fenced"
                )
                return {"success": False, "reason_code": "catalog_publish_fenced"}
            return {
                "success": True,
                "status": "completed",
                "generation": generation,
                "processed": total,
                "reason_code": "catalog_ready",
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.abandon_generation(
                generation, owner_token, reason_code="catalog_rebuild_failed"
            )
            logger.warning("话题目录回填失败", exc_info=True)
            return {"success": False, "reason_code": "catalog_rebuild_failed"}

    async def _cover_staging_dirty_sources(
        self,
        generation: int,
        owner_token: str,
        *,
        up_to_sequence: int,
        now: float,
    ) -> set[int] | None:
        """逐项重读历史 dirty source，作为 staging 覆盖证明。"""

        if self._db is None:
            return None
        cursor = await self._db.execute(
            """
            SELECT memory_id FROM topic_catalog_dirty
            WHERE sequence <= ? AND state != 'completed'
            ORDER BY sequence ASC, dirty_id ASC
            """,
            (up_to_sequence,),
        )
        covered_memory_ids: set[int] = set()
        for row in await cursor.fetchall():
            memory_id = int(row[0])
            if not await self.replace_memory_mappings(
                memory_id,
                generation,
                owner_token=owner_token,
                now=now,
            ):
                return None
            covered_memory_ids.add(memory_id)
        return covered_memory_ids
