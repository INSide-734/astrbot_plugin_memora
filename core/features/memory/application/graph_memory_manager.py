"""管理图记忆索引与同步。"""

from __future__ import annotations

import asyncio
from typing import Any

from ...recall.processors.graph_extractor import GraphExtractor
from ...retrieval.graph_vector_retriever import GraphVectorRetriever
from ..graph.infrastructure.graph_store import GraphStore


class GraphMemoryManager:
    """将图记忆产物与文档记忆存储进行同步。"""

    def __init__(
        self,
        graph_store: GraphStore,
        graph_vector_retriever: GraphVectorRetriever,
        graph_extractor: GraphExtractor,
    ) -> None:
        """保存图存储、向量检索器和抽取器，并创建变更串行锁。"""
        self.graph_store = graph_store
        self.graph_vector_retriever = graph_vector_retriever
        self.graph_extractor = graph_extractor
        self._mutation_lock = asyncio.Lock()

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
        async with self._mutation_lock:
            extracted = self.graph_extractor.extract(
                source_memory_id,
                content,
                metadata,
                atoms,
            )
            replace_result = await self.graph_store.replace_memory_graph(
                source_memory_id,
                extracted.nodes,
                extracted.edges,
                extracted.entries,
            )
            await self.graph_vector_retriever.delete_entries_for_memory(
                source_memory_id
            )
            if len(replace_result.entry_ids) != len(extracted.entries):
                raise RuntimeError(
                    "图条目标识数量不匹配: "
                    f"ids={len(replace_result.entry_ids)}, "
                    f"entries={len(extracted.entries)}"
                )
            entry_vector_doc_ids: dict[int, int] = {}
            batch_mode = False
            try:
                batch_adder = getattr(
                    type(self.graph_vector_retriever), "add_entries", None
                )
                if callable(batch_adder):
                    batch_mode = True
                    entries = [
                        (
                            entry.content,
                            {
                                **dict(entry.metadata),
                                "source_memory_id": source_memory_id,
                            },
                        )
                        for entry in extracted.entries
                    ]
                    vector_doc_ids = await self.graph_vector_retriever.add_entries(
                        entries
                    )
                    if len(vector_doc_ids) != len(extracted.entries):
                        raise RuntimeError("图向量批量插入返回的标识数量不匹配")
                    entry_vector_doc_ids = dict(
                        zip(
                            replace_result.entry_ids,
                            vector_doc_ids,
                            strict=True,
                        )
                    )
                else:
                    for entry_id, entry in zip(
                        replace_result.entry_ids,
                        extracted.entries,
                        strict=True,
                    ):
                        vector_doc_id = await self.graph_vector_retriever.add_entry(
                            entry.content,
                            dict(entry.metadata),
                        )
                        entry_vector_doc_ids[entry_id] = vector_doc_id
            except asyncio.CancelledError:
                if batch_mode:
                    await self._compensate_vector_entries(source_memory_id)
                raise
            except Exception:
                if batch_mode:
                    await self._compensate_vector_entries(source_memory_id)
                raise
            finally:
                try:
                    await self.graph_store.update_entry_vector_doc_ids(
                        entry_vector_doc_ids
                    )
                except asyncio.CancelledError:
                    if batch_mode:
                        await self._compensate_vector_entries(source_memory_id)
                    raise
                except Exception:
                    if batch_mode:
                        await self._compensate_vector_entries(source_memory_id)
                    raise

    async def _compensate_vector_entries(self, source_memory_id: int) -> None:
        """清理批量插入失败后可能留下的图向量文档。"""

        try:
            await self.graph_vector_retriever.delete_entries_for_memory(
                source_memory_id
            )
        except BaseException:
            # 保留原始失败或取消；修复日志由上层 saga 负责。
            return

    async def delete_memory(self, source_memory_id: int) -> None:
        """删除属于一条源记忆的图产物。"""
        async with self._mutation_lock:
            await self.graph_store.delete_memory(source_memory_id)
            await self.graph_vector_retriever.delete_entries_for_memory(
                source_memory_id
            )

    async def batch_delete_memories(self, source_memory_ids: list[int]) -> None:
        """批量删除多条源记忆的图产物。"""
        if not source_memory_ids:
            return
        normalized_ids = sorted({int(item) for item in source_memory_ids})
        async with self._mutation_lock:
            await self.graph_store.batch_delete_memories(normalized_ids)
            for source_memory_id in normalized_ids:
                await self.graph_vector_retriever.delete_entries_for_memory(
                    source_memory_id
                )


__all__ = ["GraphMemoryManager"]
