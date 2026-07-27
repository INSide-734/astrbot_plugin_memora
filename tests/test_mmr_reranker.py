"""测试 MMR reranker — Maximum Marginal Relevance deduplication."""

from __future__ import annotations

from typing import Any


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


class TestMMRReranker:
    def test_short_circuit_when_k_ge_results(self) -> None:
        """当 k >= len(results), all results returned unchanged."""
        from core.retrieval.mmr_reranker import apply_mmr

        results = [_make_result(1, 0.9), _make_result(2, 0.8)]
        output = apply_mmr(results, k=3, mmr_lambda=0.7)
        assert len(output) == 2
        assert output == results

    def test_empty_results(self) -> None:
        """空 input returns empty output."""
        from core.retrieval.mmr_reranker import apply_mmr

        output = apply_mmr([], k=3, mmr_lambda=0.7)
        assert output == []

    def test_deduplicates_similar_content(self) -> None:
        """MMR removes near-duplicate content from top-k."""
        from core.retrieval.mmr_reranker import apply_mmr

        results = [
            _make_result(1, 0.95, "user likes coffee and tea"),
            _make_result(2, 0.9, "user likes coffee and tea very much"),  # very similar
            _make_result(3, 0.85, "user went to the gym"),  # different
            _make_result(4, 0.8, "user likes coffee with milk"),  # similar
            _make_result(5, 0.75, "user bought a new car"),  # different
        ]
        output = apply_mmr(results, k=3, mmr_lambda=0.7)
        assert len(output) == 3
        # Doc 1 should be first (highest score)
        assert output[0].doc_id == 1

    def test_high_lambda_favors_relevance(self) -> None:
        """lambda=1.0 means pure relevance ordering (no diversity penalty)."""
        from core.retrieval.mmr_reranker import apply_mmr

        results = [
            _make_result(1, 0.9, "aaa bbb"),
            _make_result(2, 0.8, "aaa bbb ccc"),
        ]
        output = apply_mmr(results, k=2, mmr_lambda=1.0)
        assert len(output) == 2

    def test_low_lambda_favors_diversity(self) -> None:
        """lambda=0.0 means pure diversity (ignore relevance)."""
        from core.retrieval.mmr_reranker import apply_mmr

        results = [
            _make_result(1, 0.9, "aaa bbb ccc"),
            _make_result(2, 0.8, "aaa bbb ccc"),  # identical content
            _make_result(3, 0.3, "xxx yyy zzz"),  # completely different
        ]
        output = apply_mmr(results, k=2, mmr_lambda=0.0)
        assert len(output) == 2
        # First picked is highest score (doc 1)
        # Second should pick the most DIVERSE one from doc 1 — i.e. doc 3
        assert output[0].doc_id == 1
        assert output[1].doc_id == 3

    def test_identical_content_deduplicated(self) -> None:
        """多个 results with identical content — MMR picks the most diverse one for second slot."""
        from core.retrieval.mmr_reranker import apply_mmr

        results = [
            _make_result(1, 0.9, "the same exact content"),
            _make_result(2, 0.85, "the same exact content"),
            _make_result(3, 0.4, "completely different topic"),
        ]
        output = apply_mmr(results, k=2, mmr_lambda=0.5)
        assert len(output) == 2
        assert output[0].doc_id == 1  # highest score always first
        # Second should be the diverse one (doc 3), not another identical doc
        assert output[1].doc_id == 3

    def test_single_result_unaffected(self) -> None:
        """单个 result always returned as-is."""
        from core.retrieval.mmr_reranker import apply_mmr

        results = [_make_result(1, 0.5, "only one")]
        output = apply_mmr(results, k=1, mmr_lambda=0.5)
        assert output == results
