"""HybridRetriever 测试 — BM25+向量+RRF融合管线。"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest


@dataclass
class _FakeResult:
    """Minimal result object matching what HybridRetriever expects from
    BM25Retriever.search / VectorRetriever.search return values."""

    doc_id: int
    score: float
    content: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class TestHybridRetriever:
    @pytest.fixture
    def hybrid(self, test_config):
        bm25 = AsyncMock()
        bm25.search.return_value = [
            _FakeResult(doc_id=1, score=0.8, content="doc1"),
            _FakeResult(doc_id=2, score=0.5, content="doc2"),
        ]
        vector = AsyncMock()
        vector.search.return_value = [
            _FakeResult(doc_id=3, score=0.9, content="doc3"),
            _FakeResult(doc_id=1, score=0.4, content="doc1"),
        ]
        from core.retrieval.hybrid_retriever import HybridRetriever
        from core.retrieval.rrf_fusion import RRFFusion

        return HybridRetriever(bm25, vector, RRFFusion(k=60), test_config)

    @pytest.mark.asyncio
    async def test_search_merges_both_routes(self, hybrid):
        from core.retrieval.rrf_fusion import HybridResult

        results = await hybrid.search("test query", k=5)
        assert len(results) > 0
        assert all(isinstance(r, HybridResult) for r in results)

    @pytest.mark.asyncio
    async def test_limit_respected(self, hybrid):
        results = await hybrid.search("test", k=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_empty_query(self, hybrid):
        results = await hybrid.search("", k=5)
        assert isinstance(results, list)
        assert results == []

    @pytest.mark.asyncio
    async def test_score_tuple_format(self, hybrid):
        from core.retrieval.rrf_fusion import HybridResult

        results = await hybrid.search("test", k=3)
        if results:
            assert isinstance(results[0], HybridResult)
            assert isinstance(results[0].doc_id, int)
            assert isinstance(results[0].rrf_score, float)

    @pytest.mark.asyncio
    async def test_bm25_fallback_when_vector_fails(self, hybrid):
        """When vector route fails, fall back to BM25-only."""
        hybrid.vector_retriever.search.side_effect = Exception("Vector down")
        results = await hybrid.search("test", k=3)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_vector_fallback_when_bm25_fails(self, hybrid):
        """When BM25 route fails, fall back to vector-only."""
        hybrid.bm25_retriever.search.side_effect = Exception("BM25 down")
        results = await hybrid.search("test", k=3)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_both_routes_fail(self, hybrid):
        """When both routes fail, return empty list."""
        hybrid.bm25_retriever.search.side_effect = Exception("BM25 down")
        hybrid.vector_retriever.search.side_effect = Exception("Vector down")
        results = await hybrid.search("test", k=3)
        assert results == []

    @pytest.mark.asyncio
    async def test_memory_types_filter(self, hybrid):
        """memory_types filter reduces score for non-matching types."""
        # Setup: give results distinct metadata
        hybrid.bm25_retriever.search.return_value = [
            _FakeResult(
                doc_id=1, score=0.8, content="doc1", metadata={"atom_type": "EPISODIC"}
            ),
        ]
        hybrid.vector_retriever.search.return_value = [
            _FakeResult(
                doc_id=2, score=0.9, content="doc2", metadata={"atom_type": "FACTUAL"}
            ),
        ]
        results = await hybrid.search("test", k=5, memory_types=["episodic"])
        assert len(results) > 0
        # EPISODIC doc should have higher score than FACTUAL (which got *0.1)
        episodic = [r for r in results if r.doc_id == 1]
        factual = [r for r in results if r.doc_id == 2]
        if episodic and factual:
            assert episodic[0].final_score > factual[0].final_score

    @pytest.mark.asyncio
    async def test_search_route_handles_cancelled(self):
        """_search_route re-raises CancelledError."""
        import asyncio

        from core.retrieval.hybrid_retriever import HybridRetriever

        async def _cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await HybridRetriever._search_route("test", _cancelled())

    @pytest.mark.asyncio
    async def test_add_memory_delegates(self, hybrid):
        """add_memory delegates to MemoryLifecycleManager."""
        hybrid.memory_lifecycle.add_memory = AsyncMock(return_value=77)
        doc_id = await hybrid.add_memory("test", {"importance": 0.8})
        assert doc_id == 77

    @pytest.mark.asyncio
    async def test_update_metadata_delegates(self, hybrid):
        """update_metadata delegates to MemoryLifecycleManager."""
        hybrid.memory_lifecycle.update_metadata = AsyncMock(return_value=True)
        result = await hybrid.update_metadata(1, {"importance": 0.9})
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_memory_delegates(self, hybrid):
        """delete_memory delegates to MemoryLifecycleManager."""
        hybrid.memory_lifecycle.delete_memory = AsyncMock(return_value=True)
        result = await hybrid.delete_memory(1)
        assert result is True
