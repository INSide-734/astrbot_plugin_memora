"""LLMReranker 测试 — 基于LLM的结果重排序。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_result(doc_id: int, final_score: float, content: str = "") -> Any:
    from core.retrieval.rrf_fusion import HybridResult

    return HybridResult(
        doc_id=doc_id,
        final_score=final_score,
        rrf_score=final_score,
        bm25_score=None,
        vector_score=None,
        content=content or f"content_{doc_id}",
        metadata={},
    )


class TestLLMReranker:
    @pytest.fixture
    def llm_client(self) -> MagicMock:
        client = MagicMock()
        client.complete_sync = MagicMock()
        return client

    @pytest.fixture
    def reranker(self, llm_client: MagicMock) -> Any:
        from core.retrieval.llm_reranker import LLMReranker

        return LLMReranker(llm_client=llm_client, batch_size=10)

    @pytest.mark.asyncio
    async def test_rerank_no_llm_client(self) -> None:
        """Without LLM client, returns top-k by existing score."""
        from core.retrieval.llm_reranker import LLMReranker

        reranker = LLMReranker(llm_client=None)
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = await reranker.rerank(results, k=3, query="test")
        assert len(output) == 3

    @pytest.mark.asyncio
    async def test_rerank_short_circuit(self, reranker: Any) -> None:
        """When k >= len(results), no reranking needed."""
        results = [_make_result(1, 0.9), _make_result(2, 0.8)]
        output = await reranker.rerank(results, k=3, query="test")
        assert len(output) == 2

    @pytest.mark.asyncio
    async def test_rerank_empty_query(self, reranker: Any) -> None:
        """Empty query skips reranking."""
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = await reranker.rerank(results, k=3, query="")
        assert len(output) == 3

    @pytest.mark.asyncio
    async def test_rerank_with_valid_llm_response(
        self, reranker: Any, llm_client: MagicMock
    ) -> None:
        """LLM returns a valid JSON array of scores — they are blended with original."""
        llm_client.complete_sync.return_value = "[9.0, 5.0, 3.0, 1.0]"
        results = [_make_result(i, 0.8 - i * 0.1) for i in range(4)]
        output = await reranker.rerank(results, k=3, query="test query")
        assert len(output) == 3
        # Scores should be blended: (final_score + llm_score/10) / 2
        # Results should be sorted descending
        for i in range(len(output) - 1):
            assert output[i].final_score >= output[i + 1].final_score

    @pytest.mark.asyncio
    async def test_rerank_llm_returns_invalid_json(
        self, reranker: Any, llm_client: MagicMock
    ) -> None:
        """When LLM returns non-JSON, fallback to position-based scoring."""
        llm_client.complete_sync.return_value = "invalid response without array"
        results = [_make_result(i, 0.8 - i * 0.1) for i in range(5)]
        output = await reranker.rerank(results, k=3, query="test")
        assert len(output) == 3

    @pytest.mark.asyncio
    async def test_rerank_llm_raises_exception(
        self, reranker: Any, llm_client: MagicMock
    ) -> None:
        """When LLM raises, fallback to position-based scoring."""
        llm_client.complete_sync.side_effect = RuntimeError("LLM unavailable")
        results = [_make_result(i, 0.8 - i * 0.1) for i in range(5)]
        output = await reranker.rerank(results, k=3, query="test")
        assert len(output) == 3
