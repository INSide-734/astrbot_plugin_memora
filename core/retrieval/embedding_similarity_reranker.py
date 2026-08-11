"""使用 query-doc Embedding 余弦相似度进行重排序。"""

from __future__ import annotations

import math
from typing import Any

from .rrf_fusion import HybridResult


class EmbeddingSimilarityReranker:
    """以 query-document 向量余弦相似度融合原始检索分数。"""

    def __init__(self, faiss_db=None, lambda_weight: float = 0.7) -> None:
        """保存向量后端，并将融合权重限制在闭区间 ``[0, 1]``。"""

        self._faiss_db = faiss_db
        self._lambda = max(0.0, min(1.0, lambda_weight))

    def rerank(
        self, results: list[HybridResult], k: int, query: str = "", **kwargs: Any
    ) -> list[HybridResult]:
        """融合向量相似度后降序返回最多 ``k`` 项，能力失败时回退 MMR。"""

        if len(results) <= k:
            return results
        if self._faiss_db is None or not query:
            return self._fallback_mmr(results, k)
        try:
            query_vec = self._faiss_db.encode_query(query)
        except Exception:
            return self._fallback_mmr(results, k)
        for result in results:
            try:
                doc_vec = self._get_doc_vector(result.doc_id)
                if doc_vec is not None:
                    similarity_score = self._cosine_similarity(query_vec, doc_vec)
                    result.final_score = (
                        self._lambda * similarity_score
                        + (1 - self._lambda) * result.final_score
                    )
            except Exception:
                pass
        results.sort(key=lambda item: item.final_score, reverse=True)
        return results[:k]

    def _get_doc_vector(self, doc_id: int):
        """读取文档向量；后端不可用或读取失败时返回 ``None``。"""

        if self._faiss_db is None:
            return None
        try:
            return self._faiss_db.get_vector(doc_id)
        except Exception:
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度，并限制为 ``[0, 1]``。"""

        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))

    @staticmethod
    def _fallback_mmr(results: list[HybridResult], k: int) -> list[HybridResult]:
        """使用既有 MMR 默认权重完成本地安全降级。"""

        from ..shared.mmr import apply_mmr

        return apply_mmr(results, k, 0.7)


__all__ = ["EmbeddingSimilarityReranker"]
