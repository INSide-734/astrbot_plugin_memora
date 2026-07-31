"""GraphStore 的删除操作。"""

from __future__ import annotations

import json

import aiosqlite

from .base import BaseStore


class GraphDeleteMixin(BaseStore):
    """GraphStore 的删除操作。"""

    async def delete_memory(self, source_memory_id: int) -> list[int]:
        """删除属于某个源记忆的图产物。"""
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                vector_doc_ids = await self._delete_memory_rows(
                    db,
                    source_memory_id,
                )
                await self._delete_orphan_nodes(db)
                await db.commit()
                return vector_doc_ids
            except BaseException:
                await db.rollback()
                raise

    async def batch_delete_memories(
        self, source_memory_ids: list[int]
    ) -> dict[int, list[int]]:
        """批量删除多个源记忆的图产物。"""
        result: dict[int, list[int]] = {}
        if not source_memory_ids:
            return result

        normalized_ids = sorted({int(item) for item in source_memory_ids})
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                result = await self._delete_memories_rows(db, normalized_ids)
                await self._delete_orphan_nodes(db)
                await db.commit()
                return result
            except BaseException:
                await db.rollback()
                raise

    async def _delete_memory_rows(
        self,
        db: aiosqlite.Connection,
        source_memory_id: int,
    ) -> list[int]:
        """使用调用方事务删除单条源记忆的图行并返回旧向量标识。"""
        result = await self._delete_memories_rows(db, [source_memory_id])
        return result.get(source_memory_id, [])

    async def _delete_memories_rows(
        self,
        db: aiosqlite.Connection,
        source_memory_ids: list[int],
    ) -> dict[int, list[int]]:
        """使用调用方事务分批删除多条源记忆的图行。"""
        result: dict[int, list[int]] = {}
        for batch in self._chunked(source_memory_ids, self._SQLITE_BATCH_SIZE):
            batch_params = {"memory_ids_json": json.dumps(batch)}
            cursor = await db.execute(
                """
                SELECT id, source_memory_id, vector_doc_id, edge_id
                FROM graph_entries
                WHERE source_memory_id IN (
                    SELECT value FROM json_each(:memory_ids_json)
                )
                """,
                batch_params,
            )
            rows = await cursor.fetchall()
            entry_ids = [int(row[0]) for row in rows]
            edge_ids = [int(row[3]) for row in rows if row[3] is not None]
            for row in rows:
                vector_doc_id = row[2]
                if vector_doc_id is not None:
                    result.setdefault(int(row[1]), []).append(int(vector_doc_id))

            cursor = await db.execute(
                """
                SELECT id FROM graph_edges
                WHERE source_memory_id IN (
                    SELECT value FROM json_each(:memory_ids_json)
                )
                """,
                batch_params,
            )
            edge_ids.extend(int(row[0]) for row in await cursor.fetchall())
            await self._delete_entries_by_id(db, entry_ids)
            await self._delete_unreferenced_edges(db, edge_ids)
        return result

    async def _delete_entries_by_id(
        self,
        db: aiosqlite.Connection,
        entry_ids: list[int],
    ) -> None:
        """使用调用方事务删除条目、FTS 与节点关联行。"""
        for entry_batch in self._chunked(entry_ids, self._SQLITE_BATCH_SIZE):
            entry_params = {"entry_ids_json": json.dumps(entry_batch)}
            await db.execute(
                """
                DELETE FROM memora_graph_entries_fts
                WHERE entry_id IN (SELECT value FROM json_each(:entry_ids_json))
                """,
                entry_params,
            )
            await db.execute(
                """
                DELETE FROM graph_entry_nodes
                WHERE entry_id IN (SELECT value FROM json_each(:entry_ids_json))
                """,
                entry_params,
            )
            await db.execute(
                """
                DELETE FROM graph_entries
                WHERE id IN (SELECT value FROM json_each(:entry_ids_json))
                """,
                entry_params,
            )

    async def _delete_unreferenced_edges(
        self,
        db: aiosqlite.Connection,
        edge_ids: list[int],
    ) -> None:
        """使用调用方事务删除已经没有图条目引用的边。"""
        unique_edge_ids = sorted(set(edge_ids))
        for edge_batch in self._chunked(unique_edge_ids, self._SQLITE_BATCH_SIZE):
            await db.execute(
                """
                DELETE FROM graph_edges
                WHERE id IN (SELECT value FROM json_each(:edge_ids_json))
                AND NOT EXISTS (
                    SELECT 1 FROM graph_entries
                    WHERE graph_entries.edge_id = graph_edges.id
                )
                """,
                {"edge_ids_json": json.dumps(edge_batch)},
            )

    async def _delete_orphan_nodes(self, db: aiosqlite.Connection) -> None:
        """使用调用方事务删除没有边或条目引用的图节点。"""
        await db.execute(
            """
            DELETE FROM graph_nodes
            WHERE id NOT IN (
                SELECT source_node_id FROM graph_edges
                UNION
                SELECT target_node_id FROM graph_edges
                UNION
                SELECT node_id FROM graph_entry_nodes
            )
            """
        )
