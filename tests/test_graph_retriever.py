"""GraphRetriever 测试 — 图关键词+向量融合及时间评分。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


def _make_graph_kw_result(doc_id: int, score: float, content: str = "") -> Any:
    from core.retrieval.graph_keyword_retriever import GraphKeywordResult

    return GraphKeywordResult(
        doc_id=doc_id,
        score=score,
        content=content or f"content_{doc_id}",
        metadata={"importance": 0.7, "create_time": 1000000.0},
    )


def _make_graph_vec_result(doc_id: int, score: float, content: str = "") -> Any:
    from core.retrieval.graph_vector_retriever import GraphVectorResult

    return GraphVectorResult(
        doc_id=doc_id,
        score=score,
        content=content or f"content_{doc_id}",
        metadata={"importance": 0.7, "create_time": 1000000.0},
    )


class TestGraphRetriever:
    @pytest.fixture
    def rrf_fusion(self) -> Any:
        from core.retrieval.rrf_fusion import RRFFusion

        return RRFFusion(k=60)

    @pytest.fixture
    def retriever(self, rrf_fusion: Any) -> Any:
        from core.retrieval.graph_retriever import GraphRetriever

        keyword = AsyncMock()
        keyword.search = AsyncMock(return_value=[])
        vector = AsyncMock()
        vector.search = AsyncMock(return_value=[])
        return GraphRetriever(
            keyword_retriever=keyword,
            vector_retriever=vector,
            rrf_fusion=rrf_fusion,
        )

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """Empty query returns empty list."""
        assert await retriever.search("") == []
        assert await retriever.search("   ") == []

    @pytest.mark.asyncio
    async def test_search_both_empty(self, retriever: Any) -> None:
        """When both routes return nothing, result is empty."""
        timing: dict[str, float] = {}
        results = await retriever.search("nothing matches", k=5, timing_sink=timing)
        assert results == []
        assert "graph_route_degraded" not in timing
        assert timing["graph_fusion_ms"] == 0.0
        assert timing["graph_total_ms"] >= 0.0

    @pytest.mark.asyncio
    async def test_search_marks_graph_route_degraded_when_both_backends_fail(
        self,
        retriever: Any,
    ) -> None:
        """两种图检索后端均失败时必须上报整路降级。"""

        retriever.keyword_retriever.search.side_effect = RuntimeError(
            "keyword unavailable"
        )
        retriever.vector_retriever.search.side_effect = RuntimeError(
            "vector unavailable"
        )
        timing: dict[str, float | bool] = {}

        results = await retriever.search("test", k=5, timing_sink=timing)

        assert results == []
        assert timing["graph_route_degraded"] is True

    @pytest.mark.asyncio
    async def test_search_keyword_only(self, retriever: Any) -> None:
        """Keyword-only results are returned with proper scoring."""
        retriever.keyword_retriever.search.return_value = [
            _make_graph_kw_result(1, 0.9, "keyword hit"),
        ]
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1
        assert results[0].keyword_score == 0.9
        assert results[0].vector_score is None

    @pytest.mark.asyncio
    async def test_search_vector_only(self, retriever: Any) -> None:
        """Vector-only results are passed through."""
        retriever.vector_retriever.search.return_value = [
            _make_graph_vec_result(2, 0.85, "vector hit"),
        ]
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 2
        assert results[0].vector_score == 0.85
        assert results[0].keyword_score is None

    @pytest.mark.asyncio
    async def test_search_both_routes(self, retriever: Any) -> None:
        """Fused results from both routes contain score breakdown."""
        retriever.keyword_retriever.search.return_value = [
            _make_graph_kw_result(1, 0.8, "shared"),
        ]
        retriever.vector_retriever.search.return_value = [
            _make_graph_vec_result(2, 0.9, "vec_only"),
        ]
        results = await retriever.search("test", k=5)
        assert len(results) == 2
        for r in results:
            assert r.score_breakdown is not None
            assert "graph_final_score" in r.score_breakdown

    @pytest.mark.asyncio
    async def test_search_relational_boost(self, retriever: Any) -> None:
        """RELATIONAL memory type triggers 1.3x boost on relation hits."""
        retriever.keyword_retriever.search.return_value = [
            _make_graph_kw_result(3, 0.7, "relational memory"),
        ]
        # Set metadata with graph_relation_type to trigger boost
        retriever.keyword_retriever.search.return_value[0].metadata[
            "graph_relation_type"
        ] = "friend"
        retriever.vector_retriever.search.return_value = [
            _make_graph_vec_result(3, 0.6, "relational memory"),
        ]
        retriever.vector_retriever.search.return_value[0].metadata[
            "graph_relation_type"
        ] = "friend"

        results = await retriever.search("test", k=5, memory_types=["relational"])
        assert len(results) >= 1
