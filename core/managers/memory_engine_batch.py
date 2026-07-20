"""
MemoryEngine 批量操作 Mixin
提供 batch_delete_memories / _delete_document_indexes_for_batch / _delete_graph_and_atoms_for_batch
"""

from __future__ import annotations

import asyncio

from astrbot.api import logger


class MemoryEngineBatchMixin:
    """MemoryEngine 批量操作方法"""

    # ==================== 批量操作 ====================

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
            ph = ",".join("?" * len(batch))
            op_id = await self._write_journal.start_op(
                "batch_delete",
                {"memory_ids": batch, "batch_offset": i, "batch_size": len(batch)},
            )
            batch_deleted = 0
            batch_existing_ids: list[int] = []
            try:
                await self.db_connection.execute(
                    f"DELETE FROM memora_memories_fts WHERE doc_id IN ({ph})", batch
                )
                cursor = await self.db_connection.execute(
                    f"SELECT id, doc_id FROM documents WHERE id IN ({ph})", batch
                )
                uuid_rows = await cursor.fetchall()
                batch_existing_ids = [int(row["id"]) for row in uuid_rows]
                for row in uuid_rows:
                    if row["doc_id"]:
                        try:
                            await self.faiss_db.delete(row["doc_id"])
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass
                cursor = await self.db_connection.execute(
                    f"DELETE FROM documents WHERE id IN ({ph})", batch
                )
                await self.db_connection.commit()
                batch_deleted = int(cursor.rowcount or 0)
                await self._delete_graph_and_atoms_for_batch(batch)
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
            await self._write_journal.advance_op(
                op_id,
                "completed",
                status="completed",
                payload_patch={
                    "memory_ids": batch,
                    "deleted_count": batch_deleted,
                    "deleted_ids": batch_deleted_ids,
                    "not_found_ids": [mid for mid in batch if mid not in existing_set],
                },
            )
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
        ph = ",".join("?" * len(memory_ids))
        await self.db_connection.execute(
            f"DELETE FROM memora_memories_fts WHERE doc_id IN ({ph})", memory_ids
        )
        cursor = await self.db_connection.execute(
            f"SELECT id, doc_id FROM documents WHERE id IN ({ph})", memory_ids
        )
        uuid_rows = await cursor.fetchall()
        for row in uuid_rows:
            if row["doc_id"]:
                try:
                    await self.faiss_db.delete(row["doc_id"])
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[批量删除索引] FAISS 向量删除失败: {e}")
        cursor = await self.db_connection.execute(
            f"DELETE FROM documents WHERE id IN ({ph})", memory_ids
        )
        await self.db_connection.commit()
        for memory_id in memory_ids:
            await self._invalidate_evolution_after_delete(memory_id)
        return int(cursor.rowcount or 0)

    async def _delete_graph_and_atoms_for_batch(self, memory_ids: list[int]) -> None:
        if not memory_ids:
            return
        if self.graph_memory_manager is not None:
            await self.graph_memory_manager.batch_delete_memories(memory_ids)
        if self.atom_store is not None:
            await self.atom_store.batch_delete_by_parent(memory_ids)
