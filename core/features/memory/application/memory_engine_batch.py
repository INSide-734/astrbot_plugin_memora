"""
MemoryEngine 批量操作 Mixin
提供 batch_delete_memories / _delete_document_indexes_for_batch / _delete_graph_and_atoms_for_batch
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from astrbot.api import logger

from ....shared.sql import (
    MEMORY_FTS_DELETE_BY_JSON_IDS_SQL,
)
from .write_coordinator import coordinated_transaction


class MemoryEngineBatchMixin:
    """MemoryEngine 批量操作方法"""

    # ==================== 批量操作 ====================

    def _batch_vector_lock(self) -> contextlib.AbstractAsyncContextManager:
        """批量删除与正文更新（CAS → 提交 → 向量刷新）共用的向量写锁。

        未装配向量检索器的引擎没有可并发的向量刷新，不需要锁。
        """
        retriever = getattr(self, "vector_retriever", None)
        if retriever is None:
            return contextlib.nullcontext()
        return retriever._vector_write_lock

    async def batch_delete_memories_detailed(self, memory_ids: list[int]) -> dict:
        if not memory_ids or self.db_connection is None:
            return {
                "deleted_count": 0,
                "deleted_ids": [],
                "not_found_ids": list(memory_ids or []),
                "failed_ids": [],
                "errors": [],
            }
        self._retrieval.invalidate_cache()
        total_deleted = 0
        deleted_ids: list[int] = []
        not_found_ids: list[int] = []
        failed_ids: list[int] = []
        errors: list[dict] = []
        for i in range(0, len(memory_ids), 200):
            batch = memory_ids[i : i + 200]
            batch_params = {"memory_ids_json": json.dumps(batch)}
            op_id = await self._write_journal.start_op(
                "batch_delete",
                {"memory_ids": batch, "batch_offset": i, "batch_size": len(batch)},
            )
            batch_deleted = 0
            batch_existing_ids: list[int] = []
            derived_repair_needed = False
            try:
                # 向量删除与 canonical DELETE 同处向量写锁内：否则并发正文更新可能在
                # 两者之间重新写入向量，删除后留下孤儿向量。锁序固定为
                # 向量写锁 → 全局写锁，与正文更新一致。FAISS 删除经宿主自己的引擎
                # 按 doc_id 查行再删，必须先于本连接的 DELETE 执行，且不能在本连接
                # 持写锁时执行（否则跨连接互等）。
                cursor = await self.db_connection.execute(
                    """
                    SELECT id, doc_id FROM documents
                    WHERE id IN (SELECT value FROM json_each(:memory_ids_json))
                    """,
                    batch_params,
                )
                uuid_rows = await cursor.fetchall()
                await cursor.close()
                batch_existing_ids = [int(row["id"]) for row in uuid_rows]
                async with self._batch_vector_lock():
                    for row in uuid_rows:
                        if row["doc_id"]:
                            try:
                                await self.faiss_db.delete(row["doc_id"])
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                pass
                    async with coordinated_transaction(self.db_connection) as db:
                        await db.execute(
                            MEMORY_FTS_DELETE_BY_JSON_IDS_SQL, batch_params
                        )
                        cursor = await db.execute(
                            """
                            DELETE FROM documents
                            WHERE id IN (SELECT value FROM json_each(:memory_ids_json))
                            """,
                            batch_params,
                        )
                        batch_deleted = int(cursor.rowcount or 0)
                try:
                    await self._delete_graph_and_atoms_for_batch(batch)
                except asyncio.CancelledError:
                    raise
                except Exception as derived_error:
                    # canonical 删除已提交；派生清理失败只进入可恢复维护路径，
                    # 既不能回滚 canonical，也不能丢弃用户剩余的删除请求。
                    # 对外只暴露固定原因码与异常类型，避免内部错误细节进入 Page API。
                    derived_repair_needed = True
                    errors.append(
                        {
                            "batch_offset": i,
                            "stage": "derived_cleanup",
                            "reason_code": "derived_cleanup_failed",
                            "error_type": derived_error.__class__.__name__,
                            "memory_ids": batch_existing_ids[:batch_deleted],
                        }
                    )
                    await self._write_journal.advance_op(
                        op_id,
                        "batch_delete_derived_failed",
                        status="needs_repair",
                        error=str(derived_error),
                        payload_patch={
                            "memory_ids": batch,
                            "deleted_count": batch_deleted,
                        },
                    )
                    logger.warning(
                        "[批量删除] 派生清理失败，已加入修复队列 "
                        "(offset=%s, batch_size=%s, 异常类型=%s)",
                        i,
                        len(batch),
                        derived_error.__class__.__name__,
                    )
                for deleted_id in batch_existing_ids[:batch_deleted]:
                    await self._invalidate_evolution_after_delete(deleted_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failed_ids.extend(batch)
                errors.append({"batch_offset": i, "error": str(e), "memory_ids": batch})
                await self._write_journal.advance_op(
                    op_id,
                    "batch_delete_failed",
                    status="needs_repair",
                    error=str(e),
                    payload_patch={"memory_ids": batch, "deleted_count": batch_deleted},
                )
                logger.error(f"[批量删除] 批次失败 (offset={i})", exc_info=True)
                raise
            batch_deleted_ids = batch_existing_ids[:batch_deleted]
            deleted_ids.extend(batch_deleted_ids)
            existing_set = set(batch_existing_ids)
            not_found_ids.extend([mid for mid in batch if mid not in existing_set])
            if not derived_repair_needed:
                await self._write_journal.advance_op(
                    op_id,
                    "completed",
                    status="completed",
                    payload_patch={
                        "memory_ids": batch,
                        "deleted_count": batch_deleted,
                        "deleted_ids": batch_deleted_ids,
                        "not_found_ids": [
                            mid for mid in batch if mid not in existing_set
                        ],
                    },
                )
            # 派生清理待修复时保留 needs_repair 账本状态，交由 write_op_repair 重放。
            total_deleted += batch_deleted
        if total_deleted:
            logger.info(f"[批量删除] 共删除 {total_deleted} 条记忆")
        return {
            "deleted_count": total_deleted,
            "deleted_ids": deleted_ids,
            "not_found_ids": not_found_ids,
            "failed_ids": failed_ids,
            "errors": errors,
        }

    async def batch_delete_memories(self, memory_ids: list[int]) -> int:
        result = await self.batch_delete_memories_detailed(memory_ids)
        return int(result.get("deleted_count", 0))

    async def _delete_document_indexes_for_batch(self, memory_ids: list[int]) -> int:
        if not memory_ids or self.db_connection is None:
            return 0
        batch_params = {"memory_ids_json": json.dumps(memory_ids)}
        cursor = await self.db_connection.execute(
            """
            SELECT id, doc_id FROM documents
            WHERE id IN (SELECT value FROM json_each(:memory_ids_json))
            """,
            batch_params,
        )
        uuid_rows = await cursor.fetchall()
        await cursor.close()
        async with self._batch_vector_lock():
            for row in uuid_rows:
                if row["doc_id"]:
                    try:
                        await self.faiss_db.delete(row["doc_id"])
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"[批量删除索引] FAISS 向量删除失败: {e}")
            async with coordinated_transaction(self.db_connection) as db:
                await db.execute(MEMORY_FTS_DELETE_BY_JSON_IDS_SQL, batch_params)
                cursor = await db.execute(
                    """
                    DELETE FROM documents
                    WHERE id IN (SELECT value FROM json_each(:memory_ids_json))
                    """,
                    batch_params,
                )
                deleted = int(cursor.rowcount or 0)
        for memory_id in memory_ids:
            await self._invalidate_evolution_after_delete(memory_id)
        return deleted

    async def _delete_graph_and_atoms_for_batch(self, memory_ids: list[int]) -> None:
        if not memory_ids:
            return
        if self.graph_memory_manager is not None:
            await self.graph_memory_manager.batch_delete_memories(memory_ids)
        if self.atom_store is not None:
            await self.atom_store.batch_delete_by_parent(memory_ids)
