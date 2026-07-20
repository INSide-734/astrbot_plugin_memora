"""GraphVectorRetriever 测试 — 图记忆条目的向量搜索。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


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

        from core.retrieval.graph_vector_retriever import GraphVectorRetriever
        return GraphVectorRetriever(faiss_db=faiss_db)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """空查询或纯空白查询返回空列表。"""
        assert await retriever.search("") == []
        assert await retriever.search("   ") == []

    @pytest.mark.asyncio
    async def test_search_with_results(self, retriever: Any, faiss_db: MagicMock) -> None:
        """有效查询返回带分数的图向量结果。"""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 10, "text": "graph memory content",
            "metadata": {"source_memory_id": 42},
        }
        fake_result.similarity = 0.85
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("test query", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 42
        assert results[0].score == 0.85
        assert results[0].content == "graph memory content"

    @pytest.mark.asyncio
    async def test_search_skips_missing_source_memory_id(self, retriever: Any, faiss_db: MagicMock) -> None:
        """缺少 source_memory_id 的结果必须被跳过。"""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 1, "text": "orphan entry",
            "metadata": {},  # 未提供 source_memory_id
        }
        fake_result.similarity = 0.9
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("test", k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_filters(self, retriever: Any, faiss_db: MagicMock) -> None:
        """session_id 和 persona_id 会作为元数据过滤条件传递并复核。"""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 5, "text": "filtered entry",
            "metadata": {
                "source_memory_id": 1,
                "session_id": "s1",
                "persona_id": "p1",
            },
        }
        fake_result.similarity = 0.75
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("query", k=3, session_id="s1", persona_id="p1")
        assert len(results) == 1

        call_kwargs = faiss_db.retrieve.call_args.kwargs
        assert call_kwargs["metadata_filters"]["session_id"] == "s1"
        assert call_kwargs["metadata_filters"]["persona_id"] == "p1"

    @pytest.mark.asyncio
    async def test_add_entry(self, retriever: Any, faiss_db: MagicMock) -> None:
        """add_entry 委托给 faiss_db.insert。"""
        faiss_db.insert.return_value = 7
        result = await retriever.add_entry("new entry", {"key": "val"})
        assert result == 7
        faiss_db.insert.assert_called_once_with(content="new entry", metadata={"key": "val"})

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
    async def test_get_uuid_from_id_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """_get_uuid_from_id 能从文档存储解析 UUID。"""
        mock_doc = {"doc_id": "uuid-12345", "text": "content"}
        faiss_db.document_storage.get_documents.return_value = [mock_doc]
        result = await retriever._get_uuid_from_id(10)
        assert result == "uuid-12345"

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """未找到文档时 _get_uuid_from_id 返回 None。"""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever._get_uuid_from_id(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_entry_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """无法解析 UUID 时 delete_entry 返回 False。"""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.delete_entry(999)
        assert result is False
        faiss_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_entry_success(self, retriever: Any, faiss_db: MagicMock) -> None:
        """UUID 可解析时 delete_entry 通过 faiss_db 删除。"""
        faiss_db.document_storage.get_documents.return_value = [{"doc_id": "uuid-abc"}]
        result = await retriever.delete_entry(5)
        assert result is True
        faiss_db.delete.assert_called_once_with("uuid-abc")

    @pytest.mark.asyncio
    async def test_update_metadata_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """文档不存在时 update_metadata 返回 False。"""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.update_metadata(999, {"key": "val"})
        assert result is False
