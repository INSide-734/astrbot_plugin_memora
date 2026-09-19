"""图记忆路由的向量检索。"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

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
from ..memory.graph.domain.models import (
    GraphBoundary,
    GraphQueryScope,
    resolve_graph_query_scope,
)


@dataclass(slots=True)
class GraphVectorResult:
    """聚合到单条源记忆的向量匹配结果。"""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]
    source_boundary: GraphBoundary | None = None


class GraphVectorRetriever:
    """封装专用于图记忆条目的向量存储。"""

    _DELETE_BATCH_SIZE = 200
    _MAX_INSERT_BATCH_SIZE = 200
    _DEFAULT_INSERT_BATCH_SIZE = 32
    _MAX_EMBEDDING_REQUEST_ITEMS = 10

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

    @classmethod
    def _metadata_in_boundary(
        cls, raw_metadata: Any, boundary: GraphBoundary
    ) -> dict[str, Any] | None:
        """Return metadata only when its persisted boundary matches exactly."""

        metadata = cls._coerce_metadata(raw_metadata)
        try:
            return (
                metadata if GraphBoundary.from_metadata(metadata) == boundary else None
            )
        except ValueError:
            return None

    @classmethod
    def _metadata_boundary(cls, raw_metadata: Any) -> GraphBoundary | None:
        """Read the persisted source proof; rows without one stay unproven."""

        try:
            return GraphBoundary.from_metadata(cls._coerce_metadata(raw_metadata))
        except ValueError:
            return None

    async def add_entry(
        self,
        content: str,
        metadata: dict[str, Any],
        *,
        boundary: GraphBoundary | None = None,
    ) -> int:
        """将一条图条目插入向量数据库。"""

        resolved_boundary = GraphBoundary.require(boundary)
        resolved_boundary.validate_metadata(metadata)
        return await self.faiss_db.insert(
            content=content,
            metadata={**metadata, **resolved_boundary.as_params()},
        )

    async def add_entries(
        self,
        entries: list[tuple[str, dict[str, Any]]],
        *,
        batch_size: int | None = None,
        boundary: GraphBoundary | None = None,
    ) -> list[int]:
        """按外层批次写入，限制宿主 embedding 子请求并保留 ID 顺序。"""

        resolved_boundary = GraphBoundary.require(boundary)
        normalized_entries: list[tuple[str, dict[str, Any]]] = []
        for content, metadata in entries:
            resolved_boundary.validate_metadata(metadata)
            normalized_entries.append(
                (content, {**metadata, **resolved_boundary.as_params()})
            )
        if not normalized_entries:
            return []
        insert_batch = getattr(self.faiss_db, "insert_batch", None)
        if not callable(insert_batch):
            raise RuntimeError("图向量后端不支持批量插入")
        configured = self.config.get(
            "graph_embedding_batch_size",
            self.config.get("embedding_batch_size", self._DEFAULT_INSERT_BATCH_SIZE),
        )
        try:
            limit = int(configured if batch_size is None else batch_size)
        except (TypeError, ValueError):
            limit = self._DEFAULT_INSERT_BATCH_SIZE
        limit = max(1, min(limit, self._MAX_INSERT_BATCH_SIZE))
        vector_doc_ids: list[int] = []
        for start in range(0, len(normalized_entries), limit):
            chunk = normalized_entries[start : start + limit]
            ids = await cast(Callable[..., Awaitable[Any]], insert_batch)(
                contents=[content for content, _metadata in chunk],
                metadatas=[dict(metadata) for _content, metadata in chunk],
                batch_size=min(len(chunk), self._MAX_EMBEDDING_REQUEST_ITEMS),
            )
            if not isinstance(ids, (list, tuple)) or len(ids) != len(chunk):
                raise RuntimeError("图向量批量插入返回的标识数量不匹配")
            try:
                normalized = [
                    int(vector_doc_id)
                    for vector_doc_id in ids
                    if not isinstance(vector_doc_id, bool)
                ]
            except (TypeError, ValueError) as exc:
                raise RuntimeError("图向量批量插入返回的标识无效") from exc
            if len(normalized) != len(chunk):
                raise RuntimeError("图向量批量插入返回的标识无效")
            vector_doc_ids.extend(normalized)
        return vector_doc_ids

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
    ) -> list[GraphVectorResult]:
        """通过向量相似度搜索请求 scope 内的图条目。"""

        scope, revision_token = resolve_graph_query_scope(
            boundary=boundary,
            query_scope=query_scope,
        )
        if not query or not query.strip():
            return []

        metadata_filters: dict[str, Any] = dict(scope.as_params())
        if revision_token is not None:
            metadata_filters["revision_token"] = revision_token
        if session_id is not None:
            metadata_filters["session_id"] = session_id
        if persona_id is not None:
            metadata_filters["persona_id"] = persona_id

        if not self.backend_capabilities.supports(AdapterCapability.FILTERING):
            return []

        fetch_k = k * 2
        raw_results = await self.faiss_db.retrieve(
            query=query,
            k=k,
            fetch_k=fetch_k,
            rerank=False,
            metadata_filters=metadata_filters,
        )

        results: list[GraphVectorResult] = []
        for result in raw_results:
            data = result.data
            metadata = self._coerce_metadata(data.get("metadata"))
            source_boundary = self._metadata_boundary(metadata)
            if (
                source_boundary is None
                or source_boundary.scope_key != scope.scope_key
                or source_boundary.privacy_level != scope.privacy_level
                or (
                    revision_token is not None
                    and source_boundary.revision_token != revision_token
                )
                or any(
                    metadata.get(field) != expected
                    for field, expected in metadata_filters.items()
                )
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
                    source_boundary=source_boundary,
                )
            )
        return results

    async def _get_uuid_from_id(
        self,
        vector_doc_id: int,
        *,
        boundary: GraphBoundary | None = None,
    ) -> str | None:
        """解析底层向量存储使用的内部 UUID。"""

        resolved_boundary = GraphBoundary.require(boundary)
        if not self.backend_capabilities.supports(AdapterCapability.FILTERING):
            return None
        docs = await self.faiss_db.document_storage.get_documents(
            metadata_filters=resolved_boundary.as_params(),
            ids=[vector_doc_id],
            limit=1,
        )
        if not docs:
            return None
        document = docs[0]
        if (
            self._metadata_in_boundary(document.get("metadata"), resolved_boundary)
            is None
        ):
            return None
        return document.get("doc_id")

    async def delete_entry(
        self, vector_doc_id: int, *, boundary: GraphBoundary | None = None
    ) -> bool:
        """从向量存储中删除一条图条目。"""

        resolved_boundary = GraphBoundary.require(boundary)
        if not self.backend_capabilities.supports(
            AdapterCapability.DELETE
        ) or not self.backend_capabilities.supports(AdapterCapability.FILTERING):
            return False
        uuid_doc_id = await self._get_uuid_from_id(
            vector_doc_id, boundary=resolved_boundary
        )
        if not uuid_doc_id:
            return False
        await self.faiss_db.delete(uuid_doc_id)
        return True

    async def delete_entries_for_memory(
        self,
        source_memory_id: int,
        *,
        boundary: GraphBoundary | None = None,
    ) -> int:
        """删除同一图边界内属于指定源记忆的全部图向量。"""

        resolved_boundary = GraphBoundary.require(boundary)
        return await self._delete_matching_entries(
            source_memory_id, boundary=resolved_boundary
        )

    async def reap_entries_for_memory(self, source_memory_id: int) -> int:
        """跨全部 revision 与 legacy 行回收源记忆图向量；以源记忆 ID 为准。"""

        return await self._delete_matching_entries(source_memory_id, boundary=None)

    async def _delete_matching_entries(
        self,
        source_memory_id: int,
        *,
        boundary: GraphBoundary | None,
    ) -> int:
        """删除已核对来源的图向量；每个候选文档都必须证明自己的源记忆 ID。"""

        if not self.backend_capabilities.supports(AdapterCapability.DELETE):
            raise RuntimeError("图向量后端不支持删除")
        if not self.backend_capabilities.supports(AdapterCapability.FILTERING):
            raise RuntimeError("graph_vector_filtering_required")

        metadata_filters: dict[str, Any] = {"source_memory_id": source_memory_id}
        if boundary is not None:
            metadata_filters.update(boundary.as_params())
        deleted = 0
        deleted_document_ids: set[str] = set()
        while True:
            documents = await self.faiss_db.document_storage.get_documents(
                metadata_filters=metadata_filters,
                limit=self._DELETE_BATCH_SIZE,
                offset=0,
            )
            if not documents:
                return deleted

            matching_documents = []
            for document in documents:
                metadata = (
                    self._metadata_in_boundary(document.get("metadata"), boundary)
                    if boundary is not None
                    else self._coerce_metadata(document.get("metadata"))
                )
                if (
                    metadata is not None
                    and metadata.get("source_memory_id") == source_memory_id
                ):
                    matching_documents.append(document)
            if not matching_documents:
                return deleted

            document_ids = [
                document.get("doc_id")
                for document in matching_documents
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
        self,
        vector_doc_id: int,
        metadata: dict[str, Any],
        *,
        boundary: GraphBoundary | None = None,
    ) -> bool:
        """更新同一图边界内的向量文档元数据。"""

        resolved_boundary = GraphBoundary.require(boundary)
        resolved_boundary.validate_metadata(metadata)
        if not self.backend_capabilities.supports(
            AdapterCapability.UPDATE
        ) or not self.backend_capabilities.supports(AdapterCapability.FILTERING):
            return False
        docs = await self.faiss_db.document_storage.get_documents(
            metadata_filters=resolved_boundary.as_params(),
            ids=[vector_doc_id],
            limit=1,
        )
        if not docs:
            return False

        current_doc = docs[0]
        current_metadata = self._metadata_in_boundary(
            current_doc.get("metadata"), resolved_boundary
        )
        if current_metadata is None:
            return False
        merged_metadata = {
            **current_metadata,
            **metadata,
            **resolved_boundary.as_params(),
        }
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
