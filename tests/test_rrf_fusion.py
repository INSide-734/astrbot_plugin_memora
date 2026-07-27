"""测试 RRFFusion — k parameter and score normalization."""

import pytest

from core.retrieval.rrf_fusion import BM25Result, FusedResult, RRFFusion, VectorResult


def _bm25(doc_id: int, score: float, content: str = "") -> BM25Result:
    return BM25Result(doc_id=doc_id, score=score, content=content, metadata={})


def _vec(doc_id: int, score: float, content: str = "") -> VectorResult:
    return VectorResult(doc_id=doc_id, score=score, content=content, metadata={})


class TestRRFFusion:
    @pytest.fixture
    def fusion(self):
        return RRFFusion(k=60)

    def test_empty_inputs(self, fusion):
        result = fusion.fuse([], [], top_k=10)
        assert result == []

    def test_only_one_source(self, fusion):
        ranked_a = [_bm25(1, 0.9), _bm25(2, 0.5)]
        result = fusion.fuse(ranked_a, [], top_k=10)
        assert len(result) == 2
        assert all(isinstance(r, FusedResult) for r in result)

    def test_scores_are_normalized(self, fusion):
        ranked_a = [_bm25(1, 0.9)]
        ranked_b = [_vec(1, 0.5)]
        result = fusion.fuse(ranked_a, ranked_b, top_k=10)
        assert len(result) == 1
        score = result[0].rrf_score
        assert 0.0 <= score <= 1.0

    def test_k_affects_score(self):
        f_k60 = RRFFusion(k=60)
        f_k1 = RRFFusion(k=1)
        ranked_a = [_bm25(1, 0.9)]
        ranked_b = [_vec(1, 0.5)]
        r60 = f_k60.fuse(ranked_a, ranked_b, top_k=10)
        r1 = f_k1.fuse(ranked_a, ranked_b, top_k=10)
        assert r60[0].rrf_score != r1[0].rrf_score

    def test_different_ids_merged(self, fusion):
        ranked_a = [_bm25(1, 0.9)]
        ranked_b = [_vec(2, 0.8)]
        result = fusion.fuse(ranked_a, ranked_b, top_k=10)
        assert len(result) == 2

    def test_order_by_score_desc(self, fusion):
        # Doc 1: only in BM25 (rank 0) → RRF = 1/(k+0+1) = 1/61
        # Doc 2: in both BM25 (rank 1) + Vector (rank 0)
        #   → RRF = 1/(k+1+1) + 1/(k+0+1) = 1/62 + 1/61 > 1/61
        ranked_a = [_bm25(1, 0.3), _bm25(2, 0.9)]
        ranked_b = [_vec(2, 0.9)]
        result = fusion.fuse(ranked_a, ranked_b, top_k=10)
        assert result[0].doc_id == 2

    def test_duplicate_removed_highest_kept(self, fusion):
        ranked_a = [_bm25(1, 0.9), _bm25(2, 0.5)]
        ranked_b = [_vec(1, 0.4), _vec(2, 0.5)]
        result = fusion.fuse(ranked_a, ranked_b, top_k=10)
        rrf_scores = [r.rrf_score for r in result]
        assert len(result) == 2
        assert rrf_scores == sorted(rrf_scores, reverse=True)
