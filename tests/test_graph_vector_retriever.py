"""GraphVectorRetriever 测试 — 图记忆条目的向量搜索。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestGraphVectorRetriever:

    @pytest.fixture
    def faiss_db(self) -> MagicMock:
        db = MagicMock()
        db.insert = AsyncMock(return_value=1)
        db.retrieve = AsyncMock()
        db.delete = AsyncMock()
        db.document_storage = MagicMock()
        db.document_storage.get_documents = AsyncMock()
        return db

    @pytest.fixture
    def retriever(self, faiss_db: MagicMock) -> Any:
        from core.retrieval.graph_vector_retriever import GraphVectorRetriever
        return GraphVectorRetriever(faiss_db=faiss_db)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """Empty or whitespace query returns empty list."""
        assert await retriever.search("") == []
        assert await retriever.search("   ") == []

    @pytest.mark.asyncio
    async def test_search_with_results(self, retriever: Any, faiss_db: MagicMock) -> None:
        """Valid search returns scored graph vector results."""
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
        """Results without source_memory_id in metadata are skipped."""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 1, "text": "orphan entry",
            "metadata": {},  # no source_memory_id
        }
        fake_result.similarity = 0.9
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("test", k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_filters(self, retriever: Any, faiss_db: MagicMock) -> None:
        """session_id and persona_id are passed as metadata filters."""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 5, "text": "filtered entry",
            "metadata": {"source_memory_id": 1},
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
        """add_entry delegates to faiss_db.insert."""
        faiss_db.insert.return_value = 7
        result = await retriever.add_entry("new entry", {"key": "val"})
        assert result == 7
        faiss_db.insert.assert_called_once_with(content="new entry", metadata={"key": "val"})

    def test_coerce_metadata_string(self, retriever: Any) -> None:
        """_coerce_metadata parses JSON strings."""
        result = retriever._coerce_metadata('{"a": 1}')
        assert result == {"a": 1}

    def test_coerce_metadata_invalid_json(self, retriever: Any) -> None:
        """_coerce_metadata returns {} for invalid JSON."""
        result = retriever._coerce_metadata("not json")
        assert result == {}

    def test_coerce_metadata_dict_passthrough(self, retriever: Any) -> None:
        """_coerce_metadata passes through dict unchanged."""
        d = {"key": "value"}
        assert retriever._coerce_metadata(d) is d

    def test_coerce_metadata_non_dict_non_string(self, retriever: Any) -> None:
        """_coerce_metadata returns {} for non-dict/non-string input (line 37)."""
        assert retriever._coerce_metadata(42) == {}
        assert retriever._coerce_metadata(None) == {}
        assert retriever._coerce_metadata(3.14) == {}
        assert retriever._coerce_metadata([1, 2, 3]) == {}

    def test_coerce_metadata_parsed_non_dict(self, retriever: Any) -> None:
        """_coerce_metadata returns {} when JSON parses to non-dict (e.g. list)."""
        assert retriever._coerce_metadata("[1, 2, 3]") == {}

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """_get_uuid_from_id resolves UUID from document storage."""
        mock_doc = {"doc_id": "uuid-12345", "text": "content"}
        faiss_db.document_storage.get_documents.return_value = [mock_doc]
        result = await retriever._get_uuid_from_id(10)
        assert result == "uuid-12345"

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """_get_uuid_from_id returns None when no docs found."""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever._get_uuid_from_id(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_entry_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """delete_entry returns False when UUID not resolved."""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.delete_entry(999)
        assert result is False
        faiss_db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_entry_success(self, retriever: Any, faiss_db: MagicMock) -> None:
        """delete_entry deletes via faiss_db when UUID resolved."""
        faiss_db.document_storage.get_documents.return_value = [{"doc_id": "uuid-abc"}]
        result = await retriever.delete_entry(5)
        assert result is True
        faiss_db.delete.assert_called_once_with("uuid-abc")

    @pytest.mark.asyncio
    async def test_update_metadata_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """update_metadata returns False when doc not found."""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.update_metadata(999, {"key": "val"})
        assert result is False
