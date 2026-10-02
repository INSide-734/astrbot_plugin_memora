"""GraphStore 的删除操作。"""

from __future__ import annotations

import json
from collections.abc import Mapping

import aiosqlite

from ...infrastructure.base import BaseStore
from ..domain.models import GraphBoundary


def _chunked(items: list[int], size: int) -> list[list[int]]:
    """按固定大小把整数列表拆分为连续批次。"""
    step = max(1, int(size))
    return [items[index : index + step] for index in range(0, len(items), step)]


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
        """删除属于某个源记忆的图产物；共享语义边只回收失去最后 evidence 的。"""
        GraphBoundary.require(boundary)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                affected: set[int] = set()
                vector_doc_ids = await self._delete_memory_rows(
                    db,
                    source_memory_id,
                    boundary=boundary,
                    affected_semantic_ids=affected,
                )
                await self._refresh_semantic_edges(db, affected, self._now_iso())
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
                affected: set[int] = set()
                result = await self._delete_memories_rows(
                    db,
                    normalized_ids,
                    boundary=boundary,
                    affected_semantic_ids=affected,
                )
                await self._refresh_semantic_edges(db, affected, self._now_iso())
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
        affected_semantic_ids: set[int] | None = None,
    ) -> list[int]:
        """使用调用方事务删除单条源记忆的图行并返回旧向量标识。"""
        result = await self._delete_memories_rows(
            db,
            [source_memory_id],
            boundary=boundary,
            affected_semantic_ids=affected_semantic_ids,
        )
        return result.get(source_memory_id, [])

    async def _delete_memories_rows(
        self,
        db: aiosqlite.Connection,
        source_memory_ids: list[int],
        *,
        boundary: GraphBoundary | None = None,
        affected_semantic_ids: set[int] | None = None,
    ) -> dict[int, list[int]]:
        """使用调用方事务批量删除多条源记忆的 entry 与 edge evidence。

        省略 ``boundary`` 时按 canonical 源记忆回收，覆盖全部 revision 与 legacy 行。
        只删除这些来源自己的 evidence 行；被触及的语义边 ID 写入
        ``affected_semantic_ids``，由调用方在同一事务末尾调用
        ``_refresh_semantic_edges`` 汇总（未传入集合时立即汇总）。
        """
        result: dict[int, list[int]] = {}
        scope_params = self._scope_params(boundary)
        touched: set[int] = (
            affected_semantic_ids if affected_semantic_ids is not None else set()
        )
        batch_size = int(getattr(self, "_SQLITE_BATCH_SIZE", 500))
        for batch in _chunked(source_memory_ids, batch_size):
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
                SELECT id, semantic_edge_id FROM graph_edges
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
            edge_rows = await cursor.fetchall()
            edge_ids = [int(row[0]) for row in edge_rows]
            touched.update(int(row[1]) for row in edge_rows if row[1] is not None)
            await self._delete_entries_by_id(db, entry_ids)
            await self._delete_source_evidence(db, edge_ids)
        if affected_semantic_ids is None:
            await self._refresh_semantic_edges(db, touched, self._now_iso())
        return result

    async def _delete_entries_by_id(
        self,
        db: aiosqlite.Connection,
        entry_ids: list[int],
    ) -> None:
        """使用调用方事务删除条目、FTS 与节点关联行。"""
        batch_size = int(getattr(self, "_SQLITE_BATCH_SIZE", 500))
        for entry_batch in _chunked(entry_ids, batch_size):
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

    async def _delete_source_evidence(
        self,
        db: aiosqlite.Connection,
        edge_ids: list[int],
    ) -> None:
        """使用调用方事务删除这些来源自己的 edge evidence 行。

        evidence 行按来源一一归属：删除来源时它的 evidence 必须一并删除，不能因
        同来源其他 entry 仍引用而留下（调用方已先删掉该来源的全部 entry）。其他
        来源对同一语义边的 evidence 是不同行，不受影响。
        """
        unique_edge_ids = sorted(set(edge_ids))
        batch_size = int(getattr(self, "_SQLITE_BATCH_SIZE", 500))
        for edge_batch in _chunked(unique_edge_ids, batch_size):
            await db.execute(
                """
                DELETE FROM graph_edges
                WHERE id IN (SELECT value FROM json_each(:edge_ids_json))
                """,
                {"edge_ids_json": json.dumps(edge_batch)},
            )

    async def _refresh_semantic_edges(
        self,
        db: aiosqlite.Connection,
        semantic_edge_ids: set[int] | list[int],
        now: str,
    ) -> None:
        """按剩余 evidence 重算语义边聚合值，并回收失去最后 evidence 的语义边。

        聚合值取所有剩余 evidence 的最大权重/置信度；只要仍有任一 active
        evidence 语义边就保持 active。被回收的语义边上不会残留 evidence。
        """
        ids = sorted({int(item) for item in semantic_edge_ids})
        batch_size = int(getattr(self, "_SQLITE_BATCH_SIZE", 500))
        for batch in _chunked(ids, batch_size):
            params = {"ids_json": json.dumps(batch), "now": now}
            await db.execute(
                """
                DELETE FROM graph_semantic_edges
                WHERE id IN (SELECT value FROM json_each(:ids_json))
                  AND NOT EXISTS (
                    SELECT 1 FROM graph_edges evidence
                    WHERE evidence.semantic_edge_id = graph_semantic_edges.id
                  )
                """,
                params,
            )
            await db.execute(
                """
                UPDATE graph_semantic_edges
                SET weight = (
                        SELECT MAX(evidence.weight) FROM graph_edges evidence
                        WHERE evidence.semantic_edge_id = graph_semantic_edges.id
                    ),
                    confidence = (
                        SELECT MAX(evidence.confidence) FROM graph_edges evidence
                        WHERE evidence.semantic_edge_id = graph_semantic_edges.id
                    ),
                    status = CASE WHEN EXISTS (
                        SELECT 1 FROM graph_edges evidence
                        WHERE evidence.semantic_edge_id = graph_semantic_edges.id
                          AND evidence.status = 'active'
                    ) THEN 'active' ELSE 'inactive' END,
                    updated_at = :now
                WHERE id IN (SELECT value FROM json_each(:ids_json))
                """,
                params,
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
        只删除这些来源自己的 entry 与 evidence，其他来源共享的语义边保留，
        失去最后 evidence 的语义边与未被引用的节点一并回收。向量平面由调用方
        先行清理。
        """

        normalized_ids = sorted({int(item) for item in source_memory_ids})
        if not normalized_ids:
            return
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                affected: set[int] = set()
                await self._delete_memories_rows(
                    db, normalized_ids, affected_semantic_ids=affected
                )
                await self._refresh_semantic_edges(db, affected, self._now_iso())
                await self._delete_orphan_nodes(db)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def list_residual_source_memory_ids(
        self,
        canonical_memory_ids: set[int] | list[int] | tuple[int, ...],
        *,
        max_source_memory_id: int | None = None,
    ) -> list[int]:
        """列出图行仍引用、但可证明已从 canonical 删除的源记忆 ID（升序、去重）。

        ``canonical_memory_ids`` 必须是扫描结束后一次性读出的**完整**存活 ID 集合，
        ``max_source_memory_id`` 是同一次快照读到的 canonical ID 序列水位线
        （已分配的最大 ID）。``documents.id`` 由 ``AUTOINCREMENT`` 单调分配且不复用，
        因此扫描开始后新增来源的 ID 必然大于水位线，不会进入回收集合；而刚被删除的
        最新来源仍在水位线内，可正常回收。判定口径与 Atom 残留回收完全一致。

        省略水位线时退化为纯差集语义（只用于直接核对图行，不覆盖并发新增来源）；
        重建路径必须传入水位线，否则重建期间新增来源的图行会被误当残留回收。
        函数只读派生表，不触碰 canonical。
        """

        canonical_ids = {int(item) for item in canonical_memory_ids}
        watermark = None if max_source_memory_id is None else int(max_source_memory_id)
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
        return sorted(
            memory_id
            for memory_id in graph_ids - canonical_ids
            if watermark is None or memory_id <= watermark
        )

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
            batch_size = int(getattr(self, "_SQLITE_BATCH_SIZE", 500))
            for batch in _chunked(candidates, batch_size):
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
