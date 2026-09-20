"""GraphStore 的删除操作。"""

from __future__ import annotations

import json
from collections.abc import Mapping

import aiosqlite

from ...infrastructure.base import BaseStore
from ..domain.models import GraphBoundary


class GraphDeleteMixin(BaseStore):
    """GraphStore 的删除操作。"""

    @staticmethod
    def _scope_params(
        boundary: GraphBoundary | None,
    ) -> Mapping[str, str | None]:
        """生成 scope 过滤参数；NULL 表示按 canonical 源记忆跨边界回收。"""

        if boundary is None:
            return {"scope_key": None, "privacy_level": None, "revision_token": None}
        return dict(boundary.as_params())

    async def delete_memory(
        self, source_memory_id: int, *, boundary: GraphBoundary
    ) -> list[int]:
        """删除属于某个源记忆的图产物。"""
        GraphBoundary.require(boundary)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                vector_doc_ids = await self._delete_memory_rows(
                    db,
                    source_memory_id,
                    boundary=boundary,
                )
                await self._delete_orphan_nodes(db, boundary=boundary)
                await db.commit()
                return vector_doc_ids
            except BaseException:
                await db.rollback()
                raise

    async def batch_delete_memories(
        self, source_memory_ids: list[int], *, boundary: GraphBoundary
    ) -> dict[int, list[int]]:
        """批量删除多个源记忆的图产物。"""
        GraphBoundary.require(boundary)
        result: dict[int, list[int]] = {}
        if not source_memory_ids:
            return result

        normalized_ids = sorted({int(item) for item in source_memory_ids})
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                result = await self._delete_memories_rows(
                    db, normalized_ids, boundary=boundary
                )
                await self._delete_orphan_nodes(db, boundary=boundary)
                await db.commit()
                return result
            except BaseException:
                await db.rollback()
                raise

    async def _delete_memory_rows(
        self,
        db: aiosqlite.Connection,
        source_memory_id: int,
        *,
        boundary: GraphBoundary,
    ) -> list[int]:
        """使用调用方事务删除单条源记忆的图行并返回旧向量标识。"""
        result = await self._delete_memories_rows(
            db, [source_memory_id], boundary=boundary
        )
        return result.get(source_memory_id, [])

    async def _delete_memories_rows(
        self,
        db: aiosqlite.Connection,
        source_memory_ids: list[int],
        *,
        boundary: GraphBoundary | None = None,
    ) -> dict[int, list[int]]:
        """使用调用方事务批量删除多条源记忆的图行。

        省略 ``boundary`` 时按 canonical 源记忆回收，覆盖全部 revision 与 legacy 行。
        """
        result: dict[int, list[int]] = {}
        scope_params = self._scope_params(boundary)
        for batch in self._chunked(source_memory_ids, self._SQLITE_BATCH_SIZE):
            batch_params = {
                **scope_params,
                "memory_ids_json": json.dumps(batch),
            }
            cursor = await db.execute(
                """
                SELECT id, source_memory_id, vector_doc_id
                FROM graph_entries
                WHERE source_memory_id IN (
                    SELECT value FROM json_each(:memory_ids_json)
                )
                  AND (
                    :scope_key IS NULL
                    OR (
                        scope_key = :scope_key AND privacy_level = :privacy_level
                        AND revision_token = :revision_token
                    )
                  )
                """,
                batch_params,
            )
            rows = await cursor.fetchall()
            entry_ids = [int(row[0]) for row in rows]
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
                  AND (
                    :scope_key IS NULL
                    OR (
                        scope_key = :scope_key AND privacy_level = :privacy_level
                        AND revision_token = :revision_token
                    )
                  )
                """,
                batch_params,
            )
            edge_ids = [int(row[0]) for row in await cursor.fetchall()]
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

    async def _delete_orphan_nodes(
        self, db: aiosqlite.Connection, *, boundary: GraphBoundary | None = None
    ) -> None:
        """使用调用方事务删除没有边或条目引用的图节点。

        省略 ``boundary`` 时回收全部未引用节点，覆盖旧 revision 与 legacy 行。
        """
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
              AND (
                :scope_key IS NULL
                OR (
                    scope_key = :scope_key AND privacy_level = :privacy_level
                    AND revision_token = :revision_token
                )
              )
            """,
            self._scope_params(boundary),
        )

    async def reap_source_graphs(self, source_memory_ids: list[int]) -> None:
        """在一个事务内回收源记忆的全部图行。

        删除范围以 canonical 源记忆 ID 为准，覆盖全部 revision 与 legacy NULL 行；
        未被删除行引用的节点一并回收。向量平面由调用方先行清理。
        """

        normalized_ids = sorted({int(item) for item in source_memory_ids})
        if not normalized_ids:
            return
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await self._delete_memories_rows(db, normalized_ids)
                await self._delete_orphan_nodes(db)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def list_residual_source_memory_ids(
        self,
        canonical_memory_ids: set[int] | list[int] | tuple[int, ...],
    ) -> list[int]:
        """列出图行仍引用、但 canonical 已不存在的源记忆 ID（升序、去重）。

        重建只枚举当前 ``documents``，已物理删除的来源不会被枚举；本方法用
        canonical 当前 ID 集合做差集，供 ``reap_source_graphs`` 定位遗留图行。
        函数只读派生表，不触碰 canonical。
        """

        canonical_ids = {int(item) for item in canonical_memory_ids}
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT DISTINCT source_memory_id FROM graph_entries
                WHERE source_memory_id IS NOT NULL
                UNION
                SELECT DISTINCT source_memory_id FROM graph_edges
                WHERE source_memory_id IS NOT NULL
                """
            )
            graph_ids = {int(row[0]) for row in await cursor.fetchall()}
        return sorted(graph_ids - canonical_ids)

    async def list_unreferenced_vector_doc_ids(
        self, candidate_vector_doc_ids: list[int]
    ) -> list[int]:
        """返回候选中仍未被任何图条目引用的图向量文档 ID（升序、去重）。

        用于孤儿向量清理前的重新核对：只有当前图表不引用的候选才允许删除。
        """

        candidates = sorted({int(item) for item in candidate_vector_doc_ids})
        if not candidates:
            return []
        unreferenced: list[int] = []
        async with self._connect() as db:
            for batch in self._chunked(candidates, self._SQLITE_BATCH_SIZE):
                cursor = await db.execute(
                    """
                    SELECT vector_doc_id FROM graph_entries
                    WHERE vector_doc_id IN (SELECT value FROM json_each(:ids_json))
                    """,
                    {"ids_json": json.dumps(batch)},
                )
                referenced = {int(row[0]) for row in await cursor.fetchall()}
                unreferenced.extend(item for item in batch if item not in referenced)
        return sorted(unreferenced)
