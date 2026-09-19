"""管理图记忆索引与同步。"""

from __future__ import annotations

import asyncio
from typing import Any

from ....shared.memory_status import is_memory_recallable
from ...quality.application.gate_disposition_filter import is_mark_write
from ...recall.processors.graph_extractor import GraphExtractor
from ...retrieval.graph_vector_retriever import GraphVectorRetriever
from ..domain.memory_atom import has_user_source_evidence
from ..graph.domain.models import GraphBoundary
from ..graph.infrastructure.graph_store import GraphStore


def graph_source_gate_reason(metadata: Any) -> str | None:
    """返回该 canonical 来源确定不可派生的原因码；可派生时返回 ``None``。

    与 ``GraphMemoryManager.index_memory`` 共用同一判定，供批量重建按来源分类：
    legacy/归档/mark_write 与来源暂存或拒绝返回 ``graph_source_not_recallable``，
    缺逐事实用户证据返回 ``graph_source_evidence_required``，不完整 boundary
    返回 ``graph_source_boundary_invalid``。读取异常不属于确定不适用。
    """

    if not isinstance(metadata, dict):
        return "graph_source_not_recallable"
    if not is_memory_recallable(metadata) or is_mark_write(metadata):
        return "graph_source_not_recallable"
    facts = metadata.get("key_facts")
    evidence = metadata.get("fact_source_evidence")
    if (
        not isinstance(facts, list)
        or not facts
        or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
        or not isinstance(evidence, list)
        or len(evidence) != len(facts)
        or not all(has_user_source_evidence(refs) for refs in evidence)
    ):
        return "graph_source_evidence_required"
    try:
        GraphBoundary.from_metadata(metadata)
    except ValueError:
        return "graph_source_boundary_invalid"
    return None


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
        并携带各自的置信度分数。重建先按 canonical 源记忆回收旧图向量，
        再在一个事务内替换全部 revision 的图行，最后写入新向量：
        向量清理失败时旧图行与其向量映射保持不变，便于重试与修复。
        """
        async with self._mutation_lock:
            (
                canonical_content,
                canonical_metadata,
            ) = await self.graph_store.load_source_memory(source_memory_id)
            boundary = GraphBoundary.from_metadata(canonical_metadata)
            boundary.validate_metadata(metadata or {})
            gate_reason = graph_source_gate_reason(canonical_metadata)
            if gate_reason is not None:
                raise ValueError(gate_reason)
            extracted = self.graph_extractor.extract(
                source_memory_id,
                canonical_content,
                canonical_metadata,
                atoms,
            )
            await self.graph_vector_retriever.reap_entries_for_memory(source_memory_id)
            replace_result = await self.graph_store.replace_memory_graph(
                source_memory_id,
                extracted.nodes,
                extracted.edges,
                extracted.entries,
                boundary=boundary,
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
                                **boundary.as_params(),
                            },
                        )
                        for entry in extracted.entries
                    ]
                    vector_doc_ids = await self.graph_vector_retriever.add_entries(
                        entries, boundary=boundary
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
                            {
                                **dict(entry.metadata),
                                **boundary.as_params(),
                                "source_memory_id": source_memory_id,
                            },
                            boundary=boundary,
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
                        entry_vector_doc_ids, boundary=boundary
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
        """清理批量插入失败后该源记忆可能留下的全部图向量文档。"""

        try:
            await self.graph_vector_retriever.reap_entries_for_memory(source_memory_id)
        except BaseException:
            # 保留原始失败或取消；修复日志由上层 saga 负责。
            return

    async def delete_memory(self, source_memory_id: int) -> None:
        """删除属于一条源记忆的全部图产物。

        先按源记忆回收图向量，再删除图行：向量清理失败时旧图行与
        其向量映射仍然保留，重试或修复可以据此继续清理。
        """
        async with self._mutation_lock:
            await self.graph_vector_retriever.reap_entries_for_memory(source_memory_id)
            await self.graph_store.reap_source_graphs([source_memory_id])

    async def batch_delete_memories(self, source_memory_ids: list[int]) -> None:
        """批量删除多条源记忆的图产物（先向量，后一个图行事务）。"""
        if not source_memory_ids:
            return
        normalized_ids = sorted({int(item) for item in source_memory_ids})
        async with self._mutation_lock:
            for source_memory_id in normalized_ids:
                await self.graph_vector_retriever.reap_entries_for_memory(
                    source_memory_id
                )
            await self.graph_store.reap_source_graphs(normalized_ids)


__all__ = ["GraphMemoryManager"]
