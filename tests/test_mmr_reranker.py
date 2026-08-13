"""测试最大边际相关性重排与候选去重。"""

from __future__ import annotations

from typing import Any

from core.shared.mmr import apply_mmr


def _make_result(doc_id: int, final_score: float, content: str = "") -> Any:
    from core.features.retrieval.rrf_fusion import HybridResult

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
        """当 k 不小于候选数时原样返回全部结果。"""
        results = [_make_result(1, 0.9), _make_result(2, 0.8)]
        output = apply_mmr(results, k=3, mmr_lambda=0.7)
        assert len(output) == 2
        assert output == results

    def test_empty_results(self) -> None:
        """空输入应返回空结果。"""
        output = apply_mmr([], k=3, mmr_lambda=0.7)
        assert output == []

    def test_deduplicates_similar_content(self) -> None:
        """MMR 应避免近似内容占满前 k 个位置。"""
        results = [
            _make_result(1, 0.95, "user likes coffee and tea"),
            _make_result(2, 0.9, "user likes coffee and tea very much"),
            _make_result(3, 0.85, "user went to the gym"),
            _make_result(4, 0.8, "user likes coffee with milk"),
            _make_result(5, 0.75, "user bought a new car"),
        ]
        output = apply_mmr(results, k=3, mmr_lambda=0.7)
        assert len(output) == 3
        # 首个结果始终是原始分数最高的候选。
        assert output[0].doc_id == 1

    def test_high_lambda_favors_relevance(self) -> None:
        """lambda 为 1.0 时应只按相关性排序。"""
        results = [
            _make_result(1, 0.9, "aaa bbb"),
            _make_result(2, 0.8, "aaa bbb ccc"),
        ]
        output = apply_mmr(results, k=2, mmr_lambda=1.0)
        assert len(output) == 2

    def test_low_lambda_favors_diversity(self) -> None:
        """lambda 为 0.0 时应优先选择多样候选。"""
        results = [
            _make_result(1, 0.9, "aaa bbb ccc"),
            _make_result(2, 0.8, "aaa bbb ccc"),
            _make_result(3, 0.3, "xxx yyy zzz"),
        ]
        output = apply_mmr(results, k=2, mmr_lambda=0.0)
        assert len(output) == 2
        # 首个结果取最高分，第二个结果取与其差异最大的候选。
        assert output[0].doc_id == 1
        assert output[1].doc_id == 3

    def test_identical_content_deduplicated(self) -> None:
        """存在重复内容时，第二个位置应选择差异更大的候选。"""
        results = [
            _make_result(1, 0.9, "the same exact content"),
            _make_result(2, 0.85, "the same exact content"),
            _make_result(3, 0.4, "completely different topic"),
        ]
        output = apply_mmr(results, k=2, mmr_lambda=0.5)
        assert len(output) == 2
        assert output[0].doc_id == 1
        # 不应再次选择内容完全相同的候选。
        assert output[1].doc_id == 3

    def test_single_result_unaffected(self) -> None:
        """单个候选应原样返回。"""
        results = [_make_result(1, 0.5, "only one")]
        output = apply_mmr(results, k=1, mmr_lambda=0.5)
        assert output == results
