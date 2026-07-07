"""GraphStore 的删除操作。"""

from __future__ import annotations

from .base import BaseStore


class GraphDeleteMixin(BaseStore):
    """GraphStore 的删除操作。"""

    async def delete_memory(self, source_memory_id: int) -> list[int]:
        """删除属于某个源记忆的图产物。"""
        vector_doc_ids: list[int] = []
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, vector_doc_id, edge_id FROM graph_entries WHERE source_memory_id = ?",
                (source_memory_id,),
            )
            rows = await cursor.fetchall()
            entry_ids = [int(row[0]) for row in rows]
            vector_doc_ids = [int(row[1]) for row in rows if row[1] is not None]
            edge_ids = sorted({int(row[2]) for row in rows if row[2] is not None})
            cursor = await db.execute(
                "SELECT id FROM graph_edges WHERE source_memory_id = ?",
                (source_memory_id,),
            )
            edge_ids = sorted(
                set(edge_ids) | {int(row[0]) for row in await cursor.fetchall()}
            )

            if entry_ids:
                placeholders = ",".join("?" * len(entry_ids))
                await db.execute(
                    f"DELETE FROM memora_graph_entries_fts WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
                await db.execute(
                    f"DELETE FROM graph_entry_nodes WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
                await db.execute(
                    f"DELETE FROM graph_entries WHERE id IN ({placeholders})",
                    entry_ids,
                )

            if edge_ids:
                edge_placeholders = ",".join("?" * len(edge_ids))
                await db.execute(
                    f"""
                    DELETE FROM graph_edges
                    WHERE id IN ({edge_placeholders})
                    AND NOT EXISTS (
                        SELECT 1 FROM graph_entries
                        WHERE graph_entries.edge_id = graph_edges.id
                    )
                    """,
                    edge_ids,
                )
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
            await db.commit()
        return vector_doc_ids

    async def batch_delete_memories(
        self, source_memory_ids: list[int]
    ) -> dict[int, list[int]]:
        """批量删除多个源记忆的图产物。"""
        result: dict[int, list[int]] = {}
        if not source_memory_ids:
            return result

        normalized_ids = sorted({int(item) for item in source_memory_ids})
        async with self._connect() as db:
            for batch in self._chunked(normalized_ids, self._SQLITE_BATCH_SIZE):
                memory_placeholders = ",".join("?" * len(batch))

                cursor = await db.execute(
                    f"""
                    SELECT id, source_memory_id, vector_doc_id, edge_id
                    FROM graph_entries
                    WHERE source_memory_id IN ({memory_placeholders})
                    """,
                    batch,
                )
                rows = await cursor.fetchall()
                entry_ids: list[int] = []
                for row in rows:
                    entry_id = int(row[0])
                    memory_id = int(row[1])
                    vector_doc_id = row[2]
                    entry_ids.append(entry_id)
                    if vector_doc_id is not None:
                        result.setdefault(memory_id, []).append(int(vector_doc_id))
                edge_ids: list[int] = []
                for row in rows:
                    edge_id = row[3] if len(row) > 3 else None
                    if edge_id is not None:
                        edge_ids.append(int(edge_id))
                cursor = await db.execute(
                    f"""
                    SELECT id FROM graph_edges
                    WHERE source_memory_id IN ({memory_placeholders})
                    """,
                    batch,
                )
                edge_ids.extend(int(row[0]) for row in await cursor.fetchall())

                if entry_ids:
                    for entry_batch in self._chunked(
                        entry_ids,
                        self._SQLITE_BATCH_SIZE,
                    ):
                        entry_placeholders = ",".join("?" * len(entry_batch))
                        await db.execute(
                            f"DELETE FROM memora_graph_entries_fts WHERE entry_id IN ({entry_placeholders})",
                            entry_batch,
                        )
                        await db.execute(
                            f"DELETE FROM graph_entry_nodes WHERE entry_id IN ({entry_placeholders})",
                            entry_batch,
                        )
                        await db.execute(
                            f"DELETE FROM graph_entries WHERE id IN ({entry_placeholders})",
                            entry_batch,
                        )

                if edge_ids:
                    unique_edge_ids = sorted(set(edge_ids))
                    for edge_batch in self._chunked(
                        unique_edge_ids,
                        self._SQLITE_BATCH_SIZE,
                    ):
                        edge_placeholders = ",".join("?" * len(edge_batch))
                        await db.execute(
                            f"""
                            DELETE FROM graph_edges
                            WHERE id IN ({edge_placeholders})
                            AND NOT EXISTS (
                                SELECT 1 FROM graph_entries
                                WHERE graph_entries.edge_id = graph_edges.id
                            )
                            """,
                            edge_batch,
                        )

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
            await db.commit()
        return result
