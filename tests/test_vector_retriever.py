"""测试 VectorRetriever — FAISS-based dense vector retrieval."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestVectorRetriever:

    @pytest.fixture
    def faiss_db(self) -> MagicMock:
        db = MagicMock()
        db.insert = AsyncMock()
        db.retrieve = AsyncMock()
        db.delete = AsyncMock()
        db.document_storage = MagicMock()
        db.document_storage.get_documents = AsyncMock()
        # get_session() returns an object supporting async context manager
        _session_ctx = MagicMock()
        _session_ctx.__aenter__ = AsyncMock(return_value=_session_ctx)
        _session_ctx.__aexit__ = AsyncMock(return_value=None)
        _session_ctx.execute = AsyncMock()
        _session_ctx.begin = MagicMock()
        _begin_ctx = MagicMock()
        _begin_ctx.__aenter__ = AsyncMock(return_value=None)
        _begin_ctx.__aexit__ = AsyncMock(return_value=None)
        _session_ctx.begin.return_value = _begin_ctx
        _get_session = MagicMock(return_value=_session_ctx)
        db.document_storage.get_session = _get_session
        return db

    @pytest.fixture
    def retriever(self, faiss_db: MagicMock) -> Any:
        from core.retrieval.vector_retriever import VectorRetriever
        return VectorRetriever(faiss_db=faiss_db)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """空 or whitespace query returns empty list."""
        assert await retriever.search("") == []
        assert await retriever.search("   ") == []

    @pytest.mark.asyncio
    async def test_search_with_results(self, retriever: Any, faiss_db: MagicMock) -> None:
        """有效 search returns scored vector results."""
        fake_result = MagicMock()
        fake_result.data = {
            "id": 1, "text": "test memory content",
            "metadata": {"importance": 0.7},
        }
        fake_result.similarity = 0.92
        faiss_db.retrieve.return_value = [fake_result]

        results = await retriever.search("test query", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1
        assert results[0].score == 0.92
        assert results[0].content == "test memory content"

    @pytest.mark.asyncio
    async def test_search_with_filters(self, retriever: Any, faiss_db: MagicMock) -> None:
        """session_id and persona_id are passed as metadata_filters."""
        faiss_db.retrieve.return_value = []

        await retriever.search("query", k=3, session_id="s1", persona_id="p1")

        call_kwargs = faiss_db.retrieve.call_args.kwargs
        assert call_kwargs["metadata_filters"] is not None
        assert call_kwargs["metadata_filters"]["session_id"] == "s1"
        assert call_kwargs["metadata_filters"]["persona_id"] == "p1"

    @pytest.mark.asyncio
    async def test_search_fetch_k_doubled_with_filters(self, retriever: Any, faiss_db: MagicMock) -> None:
        """fetch_k is k*2 when filters are present."""
        faiss_db.retrieve.return_value = []
        await retriever.search("query", k=5, session_id="s1")
        call_kwargs = faiss_db.retrieve.call_args.kwargs
        assert call_kwargs["fetch_k"] == 10  # k * 2

    @pytest.mark.asyncio
    async def test_search_fetch_k_normal_without_filters(self, retriever: Any, faiss_db: MagicMock) -> None:
        """fetch_k equals k when no filters."""
        faiss_db.retrieve.return_value = []
        await retriever.search("query", k=5)
        call_kwargs = faiss_db.retrieve.call_args.kwargs
        assert call_kwargs["fetch_k"] == 5

    @pytest.mark.asyncio
    async def test_add_document_with_defaults(self, retriever: Any, faiss_db: MagicMock) -> None:
        """add_document injects default metadata values."""
        faiss_db.insert.return_value = 99
        doc_id = await retriever.add_document("test content")
        assert doc_id == 99
        call_args = faiss_db.insert.call_args
        metadata = call_args.kwargs.get("metadata", {})
        assert "importance" in metadata
        assert "create_time" in metadata
        assert "last_access_time" in metadata

    @pytest.mark.asyncio
    async def test_add_document_long_content_truncated(self, retriever: Any, faiss_db: MagicMock) -> None:
        """Content longer than 4000 chars is truncated for embedding."""
        faiss_db.insert.return_value = 1
        long_content = "A" * 5000
        await retriever.add_document(long_content)
        call_args = faiss_db.insert.call_args
        inserted_content = call_args.kwargs.get("content", "")
        assert len(inserted_content) <= 4001  # with marker

    @pytest.mark.asyncio
    async def test_search_long_query_truncated(self, retriever: Any, faiss_db: MagicMock) -> None:
        """查询 longer than 2000 chars is truncated."""
        faiss_db.retrieve.return_value = []
        long_query = "B" * 3000
        await retriever.search(long_query, k=5)
        call_args = faiss_db.retrieve.call_args
        processed_query = call_args.kwargs.get("query", "")
        assert len(processed_query) <= 2000

    def test_fit_content_for_embedding(self, retriever: Any) -> None:
        """_fit_content_for_embedding preserves head and tail."""
        content = "A" * 100 + "B" * 100
        result = retriever._fit_content_for_embedding(content, 50)
        assert len(result) <= 50
        # Should contain the truncation marker
        assert "截断" in result

    def test_fit_content_short_enough(self, retriever: Any) -> None:
        """Short content is returned as-is."""
        content = "short"
        result = retriever._fit_content_for_embedding(content, 100)
        assert result == "short"

    def test_fit_content_too_small_budget(self, retriever: Any) -> None:
        """当 max_chars is too small for truncation marker, simple truncation used."""
        content = "A" * 100
        # max_chars smaller than the marker length
        result = retriever._fit_content_for_embedding(content, 5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_cache_hit(self, retriever: Any) -> None:
        """_get_uuid_from_id returns cached UUID."""
        retriever._id_cache[1] = "uuid-cached-1"
        result = await retriever._get_uuid_from_id(1)
        assert result == "uuid-cached-1"

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """_get_uuid_from_id returns None when doc not found."""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever._get_uuid_from_id(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_uuid_from_id_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """_get_uuid_from_id returns UUID from DB and caches it."""
        faiss_db.document_storage.get_documents.return_value = [{"doc_id": "uuid-abc"}]
        result = await retriever._get_uuid_from_id(42)
        assert result == "uuid-abc"
        assert 42 in retriever._id_cache
        assert retriever._id_cache[42] == "uuid-abc"

    @pytest.mark.asyncio
    async def test_update_metadata_doc_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """update_metadata returns False when doc not found."""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.update_metadata(999, {"importance": 0.8})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_metadata_success(self, retriever: Any, faiss_db: MagicMock) -> None:
        """update_metadata merges metadata and returns True with dict metadata."""
        faiss_db.document_storage.get_documents.return_value = [
            {"metadata": {"importance": 0.5}}  # already a dict, not JSON string
        ]
        # Mock the sqlalchemy async session context
        # get_session is already set up in the fixture
        result = await retriever.update_metadata(1, {"importance": 0.9})
        assert result is True

    @pytest.mark.asyncio
    async def test_update_metadata_bad_json_metadata(self, retriever: Any, faiss_db: MagicMock) -> None:
        """update_metadata handles bad JSON metadata gracefully."""
        faiss_db.document_storage.get_documents.return_value = [
            {"metadata": "{bad json!!}"}
        ]
        result = await retriever.update_metadata(1, {"importance": 0.9})
        assert result is True

    @pytest.mark.asyncio
    async def test_update_metadata_string_metadata(self, retriever: Any, faiss_db: MagicMock) -> None:
        """update_metadata parses string JSON metadata."""
        faiss_db.document_storage.get_documents.return_value = [
            {"metadata": '{"importance": 0.3}'}
        ]
        result = await retriever.update_metadata(1, {"importance": 0.9})
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_document_not_found(self, retriever: Any, faiss_db: MagicMock) -> None:
        """delete_document returns False when _get_uuid_from_id returns None."""
        faiss_db.document_storage.get_documents.return_value = []
        result = await retriever.delete_document(999)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_document_success(self, retriever: Any, faiss_db: MagicMock) -> None:
        """delete_document deletes from FAISS and removes from cache."""
        retriever._id_cache[1] = "uuid-123"
        faiss_db.document_storage.get_documents.return_value = [{"doc_id": "uuid-123"}]
        faiss_db.delete.return_value = None
        result = await retriever.delete_document(1)
        assert result is True
        faiss_db.delete.assert_called_once_with("uuid-123")
        assert 1 not in retriever._id_cache
