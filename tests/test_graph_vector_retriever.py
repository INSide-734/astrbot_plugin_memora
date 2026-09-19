"""GraphVectorRetriever 测试 — 图记忆条目的向量搜索。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from core.features.memory.graph.domain.models import GraphBoundary

BOUNDARY = GraphBoundary("graph-test", "public", "r1")


class _RejectingBatchBackend:
    """模拟宿主在单次 embedding 请求超过十条时拒绝调用。"""

    def __init__(self) -> None:
        self.next_id = 100
        self.embedding_request_sizes: list[int] = []
        self.insert_call_sizes: list[int] = []
        self.records: dict[int, tuple[str, dict[str, Any]]] = {}

    async def insert_batch(
        self,
        *,
        contents: list[str],
        metadatas: list[dict[str, Any]],
        batch_size: int,
    ) -> list[int]:
        self.insert_call_sizes.append(len(contents))
        ids: list[int] = []
        for start in range(0, len(contents), batch_size):
            request_contents = contents[start : start + batch_size]
            request_metadata = metadatas[start : start + batch_size]
            if len(request_contents) > 10:
                raise RuntimeError("embedding_request_too_large")
            self.embedding_request_sizes.append(len(request_contents))
            for content, metadata in zip(
                request_contents,
                request_metadata,
                strict=True,
            ):
                vector_id = self.next_id
                self.next_id += 1
                ids.append(vector_id)
                self.records[vector_id] = (content, dict(metadata))
        return ids


class TestGraphVectorRetriever:
    """验证图向量检索、元数据规范化和底层维护委托。"""

    @pytest.fixture
    def faiss_db(self) -> MagicMock:
        """构造带异步增删查入口的 FAISS 测试替身。"""

        db = MagicMock()
        db.insert = AsyncMock(return_value=1)
        db.retrieve = AsyncMock()
        db.delete = AsyncMock()
        db.document_storage = MagicMock()
        db.document_storage.get_documents = AsyncMock()
        return db

    @pytest.fixture
    def retriever(self, faiss_db: MagicMock) -> Any:
        """使用固定 FAISS 替身构造图向量检索器。"""

        from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever

        return GraphVectorRetriever(faiss_db=faiss_db)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """空查询或纯空白查询返回空列表。"""
        assert await retriever.search("", boundary=BOUNDARY) == []
        assert await retriever.search("   ", boundary=BOUNDARY) == []

    @pytest.mark.asyncio
    async def test_search_with_results(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """有效查询返回带分数的图向量结果。"""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 10,
            "text": "graph memory content",
            "metadata": {"source_memory_id": 42, **BOUNDARY.as_params()},
        }
        fake_result.similarity = 0.85
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("test query", k=5, boundary=BOUNDARY)
        assert len(results) == 1
        assert results[0].doc_id == 42
        assert results[0].score == 0.85
        assert results[0].content == "graph memory content"

    @pytest.mark.asyncio
    async def test_search_rechecks_boundary_when_backend_returns_foreign_rows(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        metadata_rows = [
            {"source_memory_id": 1},
            {"source_memory_id": 2, **BOUNDARY.as_params(), "scope_key": "foreign"},
            {
                "source_memory_id": 3,
                **BOUNDARY.as_params(),
                "privacy_level": "confidential",
            },
            {"source_memory_id": 4, **BOUNDARY.as_params(), "revision_token": "r2"},
            {"source_memory_id": 5, **BOUNDARY.as_params()},
        ]
        faiss_db.retrieve.return_value = [
            MagicMock(
                data={"id": index, "text": f"fact-{index}", "metadata": metadata},
                similarity=1.0,
            )
            for index, metadata in enumerate(metadata_rows, 1)
        ]

        results = await retriever.search("query", k=1, boundary=BOUNDARY)

        assert [(result.doc_id, result.content) for result in results] == [
            (5, "fact-5")
        ]

    @pytest.mark.asyncio
    async def test_mutations_require_explicit_boundary(self) -> None:
        """Every graph-vector mutation rejects an absent canonical boundary."""

        from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever

        backend = _RejectingBatchBackend()
        retriever = GraphVectorRetriever(backend)
        with pytest.raises(ValueError, match="graph_boundary_required"):
            await retriever.add_entry("entry", {})
        with pytest.raises(ValueError, match="graph_boundary_required"):
            await retriever.add_entries([])
        with pytest.raises(ValueError, match="graph_boundary_required"):
            await retriever.delete_entry(1)
        with pytest.raises(ValueError, match="graph_boundary_required"):
            await retriever.delete_entries_for_memory(1)
        with pytest.raises(ValueError, match="graph_boundary_required"):
            await retriever.update_metadata(1, {})
        assert backend.records == {}

    @pytest.mark.asyncio
    async def test_search_skips_missing_source_memory_id(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """缺少 source_memory_id 的结果必须被跳过。"""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 1,
            "text": "orphan entry",
            "metadata": {},  # 未提供 source_memory_id
        }
        fake_result.similarity = 0.9
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("test", k=5, boundary=BOUNDARY)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_filters(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """session_id 和 persona_id 会作为元数据过滤条件传递并复核。"""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 5,
            "text": "filtered entry",
            "metadata": {
                **BOUNDARY.as_params(),
                "source_memory_id": 1,
                "session_id": "s1",
                "persona_id": "p1",
            },
        }
        fake_result.similarity = 0.75
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search(
            "query", k=3, session_id="s1", persona_id="p1", boundary=BOUNDARY
        )
        assert len(results) == 1

        call_kwargs = faiss_db.retrieve.call_args.kwargs
        assert call_kwargs["metadata_filters"]["session_id"] == "s1"
        assert call_kwargs["metadata_filters"]["persona_id"] == "p1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("entry_count", "expected_request_sizes"),
        ((11, [10, 1]), (32, [10, 10, 10, 2])),
    )
    async def test_add_entries_limits_embedding_requests_and_preserves_mapping(
        self,
        entry_count: int,
        expected_request_sizes: list[int],
    ) -> None:
        """大批图条目在宿主内按十条切分，返回映射仍与输入逐项对应。"""

        from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever

        backend = _RejectingBatchBackend()
        retriever = GraphVectorRetriever(backend)
        entries = [
            (
                f"entry-{index}",
                {"rank": index, "kind": "synthetic", **BOUNDARY.as_params()},
            )
            for index in range(entry_count)
        ]

        vector_ids = await retriever.add_entries(entries, boundary=BOUNDARY)

        assert backend.insert_call_sizes == [entry_count]
        assert backend.embedding_request_sizes == expected_request_sizes
        assert [backend.records[vector_id] for vector_id in vector_ids] == entries

    @pytest.mark.asyncio
    async def test_add_entries_keeps_empty_and_small_batch_behavior(self) -> None:
        """空输入不访问后端，小批输入保持单请求及逐项映射。"""

        from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever

        backend = _RejectingBatchBackend()
        retriever = GraphVectorRetriever(backend)

        assert await retriever.add_entries([], boundary=BOUNDARY) == []
        entries = [
            (content, {"rank": index, **BOUNDARY.as_params()})
            for index, content in enumerate(("a", "b", "c"), 1)
        ]
        vector_ids = await retriever.add_entries(entries, boundary=BOUNDARY)

        assert backend.insert_call_sizes == [3]
        assert backend.embedding_request_sizes == [3]
        assert [backend.records[vector_id] for vector_id in vector_ids] == entries

    def test_coerce_metadata_string(self, retriever: Any) -> None:
        """_coerce_metadata 能解析 JSON 字符串。"""
        result = retriever._coerce_metadata('{"a": 1}')
        assert result == {"a": 1}

    def test_coerce_metadata_invalid_json(self, retriever: Any) -> None:
        """无效 JSON 传入 _coerce_metadata 时返回空字典。"""
        result = retriever._coerce_metadata("not json")
        assert result == {}

    def test_coerce_metadata_dict_passthrough(self, retriever: Any) -> None:
        """_coerce_metadata 原样返回字典。"""
        d = {"key": "value"}
        assert retriever._coerce_metadata(d) is d

    def test_coerce_metadata_non_dict_non_string(self, retriever: Any) -> None:
        """非字典且非字符串输入传入 _coerce_metadata 时返回空字典。"""
        assert retriever._coerce_metadata(42) == {}
        assert retriever._coerce_metadata(None) == {}
        assert retriever._coerce_metadata(3.14) == {}
        assert retriever._coerce_metadata([1, 2, 3]) == {}

    def test_coerce_metadata_parsed_non_dict(self, retriever: Any) -> None:
        """JSON 解析结果不是字典时 _coerce_metadata 返回空字典。"""
        assert retriever._coerce_metadata("[1, 2, 3]") == {}

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_found(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """_get_uuid_from_id 能从文档存储解析 UUID。"""
        mock_doc = {
            "doc_id": "uuid-12345",
            "text": "content",
            "metadata": BOUNDARY.as_params(),
        }
        faiss_db.document_storage.get_documents.return_value = [mock_doc]
        result = await retriever._get_uuid_from_id(10, boundary=BOUNDARY)
        assert result == "uuid-12345"

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_not_found(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """未找到文档时 _get_uuid_from_id 返回 None。"""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever._get_uuid_from_id(999, boundary=BOUNDARY)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_entry_not_found(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """无法解析 UUID 时 delete_entry 返回 False。"""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.delete_entry(999, boundary=BOUNDARY)
        assert result is False
        faiss_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_entry_success(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """UUID 可解析时 delete_entry 通过 faiss_db 删除。"""
        faiss_db.document_storage.get_documents.return_value = [
            {"doc_id": "uuid-abc", "metadata": BOUNDARY.as_params()}
        ]
        result = await retriever.delete_entry(5, boundary=BOUNDARY)
        assert result is True
        faiss_db.delete.assert_called_once_with("uuid-abc")

    @pytest.mark.asyncio
    async def test_delete_entries_for_memory_removes_all_matching_documents(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """按源记忆清理会删除全部匹配向量并返回数量。"""
        faiss_db.document_storage.get_documents.side_effect = [
            [
                {
                    "doc_id": "uuid-1",
                    "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
                },
                {
                    "doc_id": "uuid-2",
                    "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
                },
            ],
            [],
        ]

        deleted = await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)

        assert deleted == 2
        faiss_db.delete.assert_has_awaits([call("uuid-1"), call("uuid-2")])
        assert faiss_db.document_storage.get_documents.await_count == 2
        expected_filters = {"source_memory_id": 36, **BOUNDARY.as_params()}
        for awaited in faiss_db.document_storage.get_documents.await_args_list:
            assert awaited.kwargs["metadata_filters"] == expected_filters
            assert awaited.kwargs["offset"] == 0

    @pytest.mark.asyncio
    async def test_delete_entries_ignores_foreign_backend_rows(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """A backend that ignores filters cannot make deletion cross a boundary."""

        foreign = GraphBoundary("foreign", "public", "r1")
        faiss_db.document_storage.get_documents.side_effect = [
            [
                {"doc_id": "foreign", "metadata": foreign.as_params()},
                {
                    "doc_id": "matching",
                    "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
                },
            ],
            [{"doc_id": "foreign", "metadata": foreign.as_params()}],
        ]

        assert (await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)) == 1

        faiss_db.delete.assert_awaited_once_with("matching")

    @pytest.mark.asyncio
    async def test_delete_entries_fails_closed_without_filtering(
        self, faiss_db: MagicMock
    ) -> None:
        """An adapter without metadata filtering cannot perform scoped deletion."""

        from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever
        from core.shared.adapter_capabilities import (
            AdapterCapability,
            AdapterCapabilityContract,
            AdapterKind,
        )

        faiss_db.adapter_capabilities = AdapterCapabilityContract(
            kind=AdapterKind.VECTOR_BACKEND,
            native=frozenset({AdapterCapability.DELETE}),
        )
        retriever = GraphVectorRetriever(faiss_db)

        with pytest.raises(RuntimeError, match="graph_vector_filtering_required"):
            await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)

        faiss_db.document_storage.get_documents.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_entries_for_memory_raises_without_progress(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """匹配文档缺少可删除标识时必须失败，不能无限重试。"""
        faiss_db.document_storage.get_documents.return_value = [
            {
                "text": "坏数据",
                "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
            }
        ]

        with pytest.raises(RuntimeError, match="缺少可删除标识"):
            await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)

        faiss_db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_entries_for_memory_raises_when_backend_rejects_delete(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """底层显式拒绝删除时必须失败，不能重复读取同一向量。"""
        faiss_db.document_storage.get_documents.return_value = [
            {
                "doc_id": "uuid-stalled",
                "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
            }
        ]
        faiss_db.delete.return_value = False

        with pytest.raises(RuntimeError, match="未取得进展"):
            await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)

        faiss_db.delete.assert_awaited_once_with("uuid-stalled")

    @pytest.mark.asyncio
    async def test_delete_entries_for_memory_raises_when_document_reappears(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """已删除 UUID 再次出现时必须失败，避免静默 no-op 无限循环。"""
        faiss_db.document_storage.get_documents.side_effect = [
            [
                {
                    "doc_id": "uuid-repeated",
                    "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
                }
            ],
            [
                {
                    "doc_id": "uuid-repeated",
                    "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
                }
            ],
        ]

        with pytest.raises(RuntimeError, match="未取得进展"):
            await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)

        faiss_db.delete.assert_awaited_once_with("uuid-repeated")

    @pytest.mark.asyncio
    async def test_delete_entries_for_memory_propagates_cancel(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """批量向量删除期间的取消必须原样传播。"""
        faiss_db.document_storage.get_documents.return_value = [
            {
                "doc_id": "uuid-cancel",
                "metadata": {"source_memory_id": 36, **BOUNDARY.as_params()},
            }
        ]
        faiss_db.delete.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await retriever.delete_entries_for_memory(36, boundary=BOUNDARY)

    @pytest.mark.asyncio
    async def test_update_metadata_not_found(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """文档不存在时 update_metadata 返回 False。"""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.update_metadata(999, {"key": "val"}, boundary=BOUNDARY)
        assert result is False

    @pytest.mark.asyncio
    async def test_vector_id_mutations_ignore_foreign_boundary_rows(
        self, retriever: Any, faiss_db: MagicMock
    ) -> None:
        """Point deletion and metadata updates refuse a foreign vector record."""

        foreign = GraphBoundary("foreign", "public", "r1")
        faiss_db.document_storage.get_documents.return_value = [
            {"doc_id": "foreign", "metadata": foreign.as_params()}
        ]

        assert await retriever.delete_entry(5, boundary=BOUNDARY) is False
        assert (
            await retriever.update_metadata(5, {"rank": 2}, boundary=BOUNDARY) is False
        )

        faiss_db.delete.assert_not_awaited()
        faiss_db.document_storage.get_session.assert_not_called()
        for awaited in faiss_db.document_storage.get_documents.await_args_list:
            assert awaited.kwargs["metadata_filters"] == BOUNDARY.as_params()
