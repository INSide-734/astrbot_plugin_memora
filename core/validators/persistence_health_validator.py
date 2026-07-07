"""只读持久化健康检查器。"""

from __future__ import annotations

from typing import Any, cast

import aiosqlite

from astrbot.api import logger

from ..storage.base import apply_perf_pragmas


class PersistenceHealthValidator:
    """检查 documents、Atom、Graph、Note、BM25、Vector 之间的不变量。"""

    def __init__(
        self,
        db_path: str,
        faiss_db: Any | None = None,
        graph_faiss_db: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.faiss_db = faiss_db
        self.graph_faiss_db = graph_faiss_db

    async def check(self) -> dict[str, Any]:
        issues: dict[str, Any] = {}
        counts: dict[str, int] = {}

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await apply_perf_pragmas(db)
                document_ids = await self._ids(db, "documents", "id")
                counts["documents"] = len(document_ids)

                await self._check_bm25(db, document_ids, issues, counts)
                await self._check_atoms(db, document_ids, issues, counts)
                await self._check_graph(db, document_ids, issues, counts)
                await self._check_notes(db, issues, counts)

            self._check_main_vectors(document_ids, issues, counts)
            self._check_graph_vectors(issues, counts)

            return {
                "ok": not bool(issues),
                "needs_repair": bool(issues),
                "counts": counts,
                "issues": issues,
            }
        except Exception as exc:
            logger.error("[持久化健康检查] 执行失败", exc_info=True)
            return {
                "ok": False,
                "needs_repair": True,
                "counts": counts,
                "issues": {"check_failed": str(exc)},
            }

    async def _check_bm25(
        self,
        db: aiosqlite.Connection,
        document_ids: set[Any],
        issues: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        if not await self._table_exists(db, "memora_memories_fts"):
            return
        bm25_ids = await self._ids(db, "memora_memories_fts", "doc_id")
        counts["bm25"] = len(bm25_ids)
        orphan = sorted(bm25_ids - document_ids)
        if orphan:
            issues["orphan_bm25_doc_ids"] = orphan

    async def _check_atoms(
        self,
        db: aiosqlite.Connection,
        document_ids: set[Any],
        issues: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        if not await self._table_exists(db, "memory_atoms"):
            return
        parent_ids = await self._ids(
            db,
            "memory_atoms",
            "parent_memory_id",
            where="parent_memory_id IS NOT NULL",
        )
        counts["memory_atoms_with_parent"] = len(parent_ids)
        orphan = sorted(parent_ids - document_ids)
        if orphan:
            issues["atom_orphan_parent_ids"] = orphan

    async def _check_graph(
        self,
        db: aiosqlite.Connection,
        document_ids: set[Any],
        issues: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        if not await self._table_exists(db, "graph_entries"):
            return
        graph_memory_ids = await self._ids(
            db,
            "graph_entries",
            "source_memory_id",
            where="source_memory_id IS NOT NULL",
        )
        counts["graph_entries_with_source"] = len(graph_memory_ids)
        orphan = sorted(graph_memory_ids - document_ids)
        if orphan:
            issues["graph_orphan_source_memory_ids"] = orphan

        vector_doc_ids = await self._ids(
            db,
            "graph_entries",
            "vector_doc_id",
            normalize=False,
            where="vector_doc_id IS NOT NULL AND vector_doc_id != ''",
        )
        counts["graph_entries_with_vector"] = len(vector_doc_ids)
        self._graph_entry_vector_ids = {str(item) for item in vector_doc_ids}

        if not await self._table_exists(db, "graph_entry_nodes"):
            return
        entry_ids = await self._ids(db, "graph_entries", "id")
        referenced_entry_ids = await self._ids(db, "graph_entry_nodes", "entry_id")
        orphan_entry_ids = sorted(referenced_entry_ids - entry_ids)
        if orphan_entry_ids:
            issues["graph_entry_nodes_orphan_entry_ids"] = orphan_entry_ids

        if await self._table_exists(db, "graph_nodes"):
            node_ids = await self._ids(db, "graph_nodes", "id")
            referenced_node_ids = await self._ids(db, "graph_entry_nodes", "node_id")
            orphan_node_ids = sorted(referenced_node_ids - node_ids)
            if orphan_node_ids:
                issues["graph_entry_nodes_orphan_node_ids"] = orphan_node_ids

    async def _check_notes(
        self,
        db: aiosqlite.Connection,
        issues: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        if not (
            await self._table_exists(db, "notes")
            and await self._table_exists(db, "note_versions")
        ):
            return
        note_ids = await self._ids(db, "notes", "id")
        version_note_ids = await self._ids(db, "note_versions", "note_id")
        counts["notes"] = len(note_ids)
        counts["note_versions"] = len(version_note_ids)
        orphan = sorted(version_note_ids - note_ids)
        if orphan:
            issues["orphan_note_version_note_ids"] = orphan

        cursor = await db.execute(
            """
            SELECT note_id, version, COUNT(*) AS c
            FROM note_versions
            GROUP BY note_id, version
            HAVING c > 1
            ORDER BY note_id, version
            """
        )
        duplicates = [
            {
                "note_id": self._normalize_id(row[0]),
                "version": self._normalize_id(row[1]),
                "count": int(row[2]),
            }
            for row in await cursor.fetchall()
        ]
        if duplicates:
            issues["duplicate_note_versions"] = duplicates

    def _check_main_vectors(
        self,
        document_ids: set[Any],
        issues: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        vector_ids = self._get_vector_ids(self.faiss_db)
        if vector_ids is None:
            return
        normalized = {self._normalize_id(item) for item in vector_ids}
        counts["main_vectors"] = len(normalized)
        orphan = sorted(normalized - document_ids)
        if orphan:
            issues["orphan_main_vector_ids"] = orphan

    def _check_graph_vectors(
        self,
        issues: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        graph_vector_ids = self._get_vector_ids(self.graph_faiss_db)
        if graph_vector_ids is None:
            return
        normalized = {str(item) for item in graph_vector_ids}
        counts["graph_vectors"] = len(normalized)
        entry_vector_ids = getattr(self, "_graph_entry_vector_ids", set())
        orphan = sorted(normalized - entry_vector_ids)
        if orphan:
            issues["orphan_graph_vector_ids"] = orphan

    async def _table_exists(self, db: aiosqlite.Connection, table_name: str) -> bool:
        cursor = await db.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type IN ('table', 'view') AND name = ?
            """,
            (table_name,),
        )
        return await cursor.fetchone() is not None

    async def _ids(
        self,
        db: aiosqlite.Connection,
        table_name: str,
        column_name: str,
        *,
        where: str | None = None,
        normalize: bool = True,
    ) -> set[Any]:
        clause = f" WHERE {where}" if where else ""
        cursor = await db.execute(f"SELECT DISTINCT {column_name} FROM {table_name}{clause}")
        values = {row[0] for row in await cursor.fetchall() if row[0] is not None}
        if not normalize:
            return values
        return {self._normalize_id(value) for value in values}

    def _get_vector_ids(self, vector_db: Any | None) -> set[Any] | None:
        if vector_db is None:
            return None
        embedding_storage = getattr(vector_db, "embedding_storage", None)
        index = getattr(embedding_storage, "index", None)
        if index is None:
            return None
        try:
            import faiss

            if hasattr(index, "id_map"):
                vector_to_array = getattr(faiss, "vector_to_array", None)
                if callable(vector_to_array):
                    raw_ids = cast(Any, vector_to_array(index.id_map))
                    return set(raw_ids)
        except Exception as exc:
            logger.debug("[持久化健康检查] 读取向量 ID 失败: %s", exc)
        return None

    @staticmethod
    def _normalize_id(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        try:
            text = str(value).strip()
            if text and text.lstrip("-").isdigit():
                return int(text)
        except Exception:
            return value
        return value


__all__ = ["PersistenceHealthValidator"]
