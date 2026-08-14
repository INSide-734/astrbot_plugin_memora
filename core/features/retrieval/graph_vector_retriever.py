"""图记忆路由的向量检索。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ...shared.adapter_capabilities import (
    ASTRBOT_FAISS_CAPABILITIES,
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
    bind_default_adapter_contract,
)


@dataclass(slots=True)
class GraphVectorResult:
    """聚合到单条源记忆的向量匹配结果。"""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]


class GraphVectorRetriever:
    """封装专用于图记忆条目的向量存储。"""

    _DELETE_BATCH_SIZE = 200

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.VECTOR_RETRIEVER,
        native=frozenset({AdapterCapability.SCORING}),
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.UPDATE,
                AdapterCapability.DELETE,
                AdapterCapability.CANCELLATION,
            }
        ),
        score=ScoreSemantics(
            direction=ScoreDirection.HIGHER_IS_BETTER,
            normalization=NormalizationScope.UNKNOWN,
        ),
    )

    def __init__(self, faiss_db, config: dict[str, Any] | None = None):
        """装配图条目向量数据库与检索配置。"""

        self.faiss_db = faiss_db
        self.backend_capabilities = bind_default_adapter_contract(
            faiss_db,
            ASTRBOT_FAISS_CAPABILITIES,
        )
        self.config = config or {}

    @staticmethod
    def _coerce_metadata(raw_metadata: Any) -> dict[str, Any]:
        """把字典或 JSON 字符串规范化为元数据字典。"""

        if isinstance(raw_metadata, dict):
            return raw_metadata
        if isinstance(raw_metadata, str):
            try:
                parsed = json.loads(raw_metadata)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    async def add_entry(self, content: str, metadata: dict[str, Any]) -> int:
        """将一条图条目插入向量数据库。"""
        return await self.faiss_db.insert(content=content, metadata=metadata)

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[GraphVectorResult]:
        """通过向量相似度搜索图条目。"""
        if not query or not query.strip():
            return []

        metadata_filters: dict[str, Any] = {}
        if session_id is not None:
            metadata_filters["session_id"] = session_id
        if persona_id is not None:
            metadata_filters["persona_id"] = persona_id

        if metadata_filters and not self.backend_capabilities.supports(
            AdapterCapability.FILTERING
        ):
            return []

        fetch_k = k * 2 if metadata_filters else k
        raw_results = await self.faiss_db.retrieve(
            query=query,
            k=k,
            fetch_k=fetch_k,
            rerank=False,
            metadata_filters=metadata_filters if metadata_filters else None,
        )

        results: list[GraphVectorResult] = []
        for result in raw_results:
            data = result.data
            metadata = self._coerce_metadata(data.get("metadata"))
            if metadata_filters and any(
                metadata.get(field) != expected
                for field, expected in metadata_filters.items()
            ):
                continue
            source_memory_id = metadata.get("source_memory_id")
            if source_memory_id is None:
                continue
            similarity = float(result.similarity)
            if not math.isfinite(similarity):
                continue
            results.append(
                GraphVectorResult(
                    doc_id=int(source_memory_id),
                    score=similarity,
                    content=str(data.get("text") or ""),
                    metadata=metadata,
                )
            )
        return results

    async def _get_uuid_from_id(self, vector_doc_id: int) -> str | None:
        """解析底层向量存储使用的内部 UUID。"""
        docs = await self.faiss_db.document_storage.get_documents(
            metadata_filters={},
            ids=[vector_doc_id],
            limit=1,
        )
        if not docs:
            return None
        return docs[0].get("doc_id")

    async def delete_entry(self, vector_doc_id: int) -> bool:
        """从向量存储中删除一条图条目。"""
        if not self.backend_capabilities.supports(AdapterCapability.DELETE):
            return False
        uuid_doc_id = await self._get_uuid_from_id(vector_doc_id)
        if not uuid_doc_id:
            return False
        await self.faiss_db.delete(uuid_doc_id)
        return True

    async def delete_entries_for_memory(self, source_memory_id: int) -> int:
        """删除属于指定源记忆的全部图向量，并返回删除数量。"""
        if not self.backend_capabilities.supports(AdapterCapability.DELETE):
            raise RuntimeError("图向量后端不支持删除")

        deleted = 0
        deleted_document_ids: set[str] = set()
        while True:
            documents = await self.faiss_db.document_storage.get_documents(
                metadata_filters={"source_memory_id": source_memory_id},
                limit=self._DELETE_BATCH_SIZE,
                offset=0,
            )
            if not documents:
                return deleted

            document_ids = [
                document.get("doc_id")
                for document in documents
                if document.get("doc_id")
            ]
            if not document_ids:
                raise RuntimeError("图向量文档缺少可删除标识")
            normalized_ids = {str(document_id) for document_id in document_ids}
            if normalized_ids & deleted_document_ids:
                raise RuntimeError("图向量删除未取得进展")

            for document_id in document_ids:
                delete_result = await self.faiss_db.delete(document_id)
                if delete_result is False:
                    raise RuntimeError("图向量删除未取得进展")
                deleted += 1
                deleted_document_ids.add(str(document_id))

    async def update_metadata(
        self, vector_doc_id: int, metadata: dict[str, Any]
    ) -> bool:
        """更新向量文档存储中的图条目元数据。"""
        if not self.backend_capabilities.supports(AdapterCapability.UPDATE):
            return False
        docs = await self.faiss_db.document_storage.get_documents(
            metadata_filters={},
            ids=[vector_doc_id],
            limit=1,
        )
        if not docs:
            return False

        current_doc = docs[0]
        merged_metadata = dict(self._coerce_metadata(current_doc.get("metadata")))
        merged_metadata.update(metadata)
        async with (
            self.faiss_db.document_storage.get_session() as session,
            session.begin(),
        ):
            from sqlalchemy import text

            await session.execute(
                text("UPDATE documents SET metadata = :metadata WHERE id = :id"),
                {
                    "metadata": json.dumps(merged_metadata, ensure_ascii=False),
                    "id": vector_doc_id,
                },
            )
        return True


__all__ = ["GraphVectorRetriever", "GraphVectorResult"]
