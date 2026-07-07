"""CrossEncoderReranker 测试 — 基于Embedding的重排序。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_result(doc_id: int, final_score: float) -> Any:
    from core.retrieval.rrf_fusion import HybridResult
    return HybridResult(
        doc_id=doc_id,
        final_score=final_score,
        rrf_score=final_score,
        bm25_score=None,
        vector_score=None,
        content=f"content_{doc_id}",
        metadata={},
    )


class TestCrossEncoderReranker:

    @pytest.fixture
    def faiss_db(self) -> MagicMock:
        db = MagicMock()
        db.encode_query = MagicMock(return_value=[0.1, 0.2, 0.3])
        db.get_vector = MagicMock(return_value=[0.1, 0.2, 0.3])
        return db

    @pytest.fixture
    def reranker(self, faiss_db: MagicMock) -> Any:
        from core.retrieval.cross_encoder_reranker import CrossEncoderReranker
        return CrossEncoderReranker(faiss_db=faiss_db, lambda_weight=0.7)

    def test_rerank_short_circuit_when_k_exceeds_results(self, reranker: Any) -> None:
        """When k >= len(results), return results unchanged."""
        results = [_make_result(1, 0.9), _make_result(2, 0.8)]
        output = reranker.rerank(results, k=3, query="test")
        assert len(output) == 2
        assert output[0].doc_id == 1

    def test_rerank_falls_back_when_faiss_none(self) -> None:
        """When FAISS is None, falls back to MMR rerank."""
        from core.retrieval.cross_encoder_reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(faiss_db=None)
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = reranker.rerank(results, k=3, query="test")
        # MMR fallback should produce results
        assert len(output) == 3

    def test_rerank_falls_back_when_query_empty(self, reranker: Any) -> None:
        """Empty query triggers MMR fallback."""
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = reranker.rerank(results, k=3, query="")
        assert len(output) == 3

    def test_rerank_falls_back_on_encode_error(self, reranker: Any, faiss_db: MagicMock) -> None:
        """When encode_query raises, use MMR fallback."""
        faiss_db.encode_query.side_effect = RuntimeError("encode failed")
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = reranker.rerank(results, k=3, query="test")
        assert len(output) == 3

    def test_rerank_combines_scores(self, reranker: Any, faiss_db: MagicMock) -> None:
        """Scores are combined: lambda * ce_score + (1-lambda) * final_score."""
        faiss_db.encode_query.return_value = [0.5, 0.5, 0.5]
        faiss_db.get_vector.return_value = [0.5, 0.5, 0.5]
        results = [_make_result(i, 0.8 - i * 0.1) for i in range(5)]
        output = reranker.rerank(results, k=3, query="test")
        assert len(output) == 3
        # All should be sorted descending
        for i in range(len(output) - 1):
            assert output[i].final_score >= output[i + 1].final_score

    def test_cosine_similarity(self, reranker: Any) -> None:
        """Static _cosine_similarity computes correctly."""
        from core.retrieval.cross_encoder_reranker import CrossEncoderReranker
        # Same vector → 1.0
        assert CrossEncoderReranker._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
        # Orthogonal → 0.0
        assert CrossEncoderReranker._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
        # Zero norm → 0.0
        assert CrossEncoderReranker._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_lambda_clamped_to_range(self) -> None:
        """lambda_weight is clamped to [0, 1]."""
        from core.retrieval.cross_encoder_reranker import CrossEncoderReranker
        r1 = CrossEncoderReranker(faiss_db=MagicMock(), lambda_weight=5.0)
        assert r1._lambda == 1.0  # type: ignore[attr-defined]
        r2 = CrossEncoderReranker(faiss_db=MagicMock(), lambda_weight=-1.0)
        assert r2._lambda == 0.0  # type: ignore[attr-defined]
