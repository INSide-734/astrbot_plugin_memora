"""管理图记忆索引与同步。"""

from __future__ import annotations

from typing import Any

from ..processors.graph_extractor import GraphExtractor
from ..retrieval.graph_vector_retriever import GraphVectorRetriever
from ..storage.graph_store import GraphStore


class GraphMemoryManager:
    """将图记忆产物与文档记忆存储进行同步。"""

    def __init__(
        self,
        graph_store: GraphStore,
        graph_vector_retriever: GraphVectorRetriever,
        graph_extractor: GraphExtractor,
    ):
        self.graph_store = graph_store
        self.graph_vector_retriever = graph_vector_retriever
        self.graph_extractor = graph_extractor

    async def index_memory(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
        atoms: list | None = None,
    ) -> None:
        """为一条源记忆重建图产物。

        当提供原子时，每个原子独立贡献节点/边/条目，
        并携带各自的置信度分数。
        """
        await self.delete_memory(source_memory_id)

        extracted = self.graph_extractor.extract(
            source_memory_id, content, metadata, atoms
        )
        if not extracted.entries:
            return

        node_key_to_id = await self.graph_store.upsert_nodes(extracted.nodes)

        edge_key_to_id = await self.graph_store.add_edges(
            extracted.edges,
            node_key_to_id,
        )

        entry_ids = await self.graph_store.add_entries(
            extracted.entries,
            node_key_to_id,
            edge_key_to_id,
        )
        if len(entry_ids) != len(extracted.entries):
            raise RuntimeError(
                "graph entry id count mismatch: "
                f"ids={len(entry_ids)}, entries={len(extracted.entries)}"
            )
        entry_vector_doc_ids: dict[int, int] = {}
        try:
            for entry_id, entry in zip(entry_ids, extracted.entries, strict=True):
                vector_doc_id = await self.graph_vector_retriever.add_entry(
                    entry.content,
                    dict(entry.metadata),
                )
                entry_vector_doc_ids[entry_id] = vector_doc_id
        finally:
            await self.graph_store.update_entry_vector_doc_ids(entry_vector_doc_ids)

    async def delete_memory(self, source_memory_id: int) -> None:
        """删除属于一条源记忆的图产物。"""
        vector_doc_ids = await self.graph_store.delete_memory(source_memory_id)
        for vector_doc_id in vector_doc_ids:
            await self.graph_vector_retriever.delete_entry(vector_doc_id)

    async def batch_delete_memories(self, source_memory_ids: list[int]) -> None:
        """批量删除多条源记忆的图产物。"""
        if not source_memory_ids:
            return
        memory_vec_map = await self.graph_store.batch_delete_memories(source_memory_ids)
        for vector_doc_ids in memory_vec_map.values():
            for vector_doc_id in vector_doc_ids:
                await self.graph_vector_retriever.delete_entry(vector_doc_id)


__all__ = ["GraphMemoryManager"]
