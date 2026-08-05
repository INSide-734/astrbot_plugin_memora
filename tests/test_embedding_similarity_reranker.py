"""Embedding 相似度重排序器的行为与命名契约测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_result(doc_id: int, final_score: float) -> Any:
    """构造只包含重排序所需字段的检索结果。"""

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


@pytest.fixture
def faiss_db() -> MagicMock:
    """构造能够编码查询并读取文档向量的测试后端。"""

    backend = MagicMock()
    backend.encode_query.return_value = [0.1, 0.2, 0.3]
    backend.get_vector.return_value = [0.1, 0.2, 0.3]
    return backend


@pytest.fixture
def reranker(faiss_db: MagicMock) -> Any:
    """构造使用默认融合权重的 Embedding 相似度重排器。"""

    from core.retrieval.embedding_similarity_reranker import (
        EmbeddingSimilarityReranker,
    )

    return EmbeddingSimilarityReranker(faiss_db=faiss_db, lambda_weight=0.7)


def test_rerank_short_circuits_when_k_exceeds_results(reranker: Any) -> None:
    """候选数不超过 ``k`` 时应保持原列表和顺序。"""

    results = [_make_result(1, 0.9), _make_result(2, 0.8)]
    output = reranker.rerank(results, k=3, query="test")
    assert output is results
    assert [item.doc_id for item in output] == [1, 2]


def test_rerank_falls_back_when_backend_is_missing() -> None:
    """缺少向量后端时应回退 MMR 并返回有界结果。"""

    from core.retrieval.embedding_similarity_reranker import (
        EmbeddingSimilarityReranker,
    )

    local_reranker = EmbeddingSimilarityReranker(faiss_db=None)
    results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
    assert len(local_reranker.rerank(results, k=3, query="test")) == 3


def test_rerank_falls_back_when_query_is_empty(reranker: Any) -> None:
    """空查询无法计算相似度时应回退 MMR。"""

    results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
    assert len(reranker.rerank(results, k=3, query="")) == 3


def test_rerank_falls_back_when_query_encoding_fails(
    reranker: Any,
    faiss_db: MagicMock,
) -> None:
    """查询编码失败时应回退 MMR，避免中断召回主链。"""

    faiss_db.encode_query.side_effect = RuntimeError("encode failed")
    results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
    assert len(reranker.rerank(results, k=3, query="test")) == 3


def test_cosine_similarity_handles_equal_orthogonal_and_zero_vectors() -> None:
    """余弦计算应覆盖同向、正交和零范数边界。"""

    from core.retrieval.embedding_similarity_reranker import (
        EmbeddingSimilarityReranker,
    )

    assert EmbeddingSimilarityReranker._cosine_similarity(
        [1.0, 0.0], [1.0, 0.0]
    ) == pytest.approx(1.0)
    assert EmbeddingSimilarityReranker._cosine_similarity(
        [1.0, 0.0], [0.0, 1.0]
    ) == pytest.approx(0.0)
    assert EmbeddingSimilarityReranker._cosine_similarity(
        [0.0, 0.0], [1.0, 0.0]
    ) == pytest.approx(0.0)


def test_lambda_weight_is_clamped_to_supported_range() -> None:
    """越界融合权重应分别收敛到 ``0`` 和 ``1``。"""

    from core.retrieval.embedding_similarity_reranker import (
        EmbeddingSimilarityReranker,
    )

    upper = EmbeddingSimilarityReranker(MagicMock(), lambda_weight=5.0)
    lower = EmbeddingSimilarityReranker(MagicMock(), lambda_weight=-1.0)
    assert upper._lambda == 1.0  # type: ignore[attr-defined]
    assert lower._lambda == 0.0  # type: ignore[attr-defined]


def test_embedding_similarity_preserves_legacy_order_and_scores() -> None:
    """纯重命名阶段应保持既有 cosine 融合公式、排序和分数不变。"""

    from core.retrieval.embedding_similarity_reranker import (
        EmbeddingSimilarityReranker,
    )

    backend = MagicMock()
    backend.encode_query.return_value = [1.0, 0.0]
    backend.get_vector.side_effect = {
        1: [1.0, 0.0],
        2: [0.0, 1.0],
        3: [1.0, 1.0],
    }.get
    reranker = EmbeddingSimilarityReranker(backend, lambda_weight=0.7)
    results = [
        _make_result(1, 0.2),
        _make_result(2, 0.9),
        _make_result(3, 0.5),
    ]

    ranked = reranker.rerank(results, 2, query="query")

    assert [item.doc_id for item in ranked] == [1, 3]
    assert ranked[0].final_score == pytest.approx(0.76)
    assert ranked[1].final_score == pytest.approx(0.7 / (2**0.5) + 0.15)
