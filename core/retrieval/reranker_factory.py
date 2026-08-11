"""可插拔重排序策略工厂 — MMR / Embedding 相似度 / LLM / Hybrid。"""

from __future__ import annotations

from typing import Any, Protocol

from ..shared.adapter_capabilities import (
    AdapterCapability,
    adapter_contract,
)
from ..shared.mmr import MMRReranker
from .rrf_fusion import HybridResult


class RerankerStrategy(Protocol):
    """统一重排器的结构化调用契约。"""

    def rerank(
        self, results: list[HybridResult], k: int, **kwargs: Any
    ) -> list[HybridResult]:
        """按策略重排候选并返回最多 ``k`` 项。"""

        ...


async def create_reranker(
    strategy: str, config: dict[str, Any] | None = None, **deps: Any
):
    """按策略和显式依赖能力构造重排器，能力不足时降级为 MMR。

    参数:
        strategy: ``mmr``、``embedding_similarity``、``llm`` 或 ``hybrid``。
        config: 重排器权重和批大小配置。
        deps: FAISS 与 LLM 协作对象；兼容旧调用方的嵌套 ``deps`` 字典。

    返回:
        已构造的重排器；外部依赖能力不足时返回带稳定原因码的 MMR。
    """

    nested_deps = deps.get("deps")
    if isinstance(nested_deps, dict):
        direct_deps = {key: value for key, value in deps.items() if key != "deps"}
        deps = {**nested_deps, **direct_deps}
    cfg = config or {}
    if strategy == "embedding_similarity":
        from .embedding_similarity_reranker import EmbeddingSimilarityReranker

        faiss_db = deps.get("faiss_db")
        if not adapter_contract(faiss_db).supports(AdapterCapability.VECTOR_ACCESS):
            return MMRReranker(
                float(cfg.get("reranker.mmr_lambda", 0.7)),
                degradation_reason_code="adapter_capability_unsupported",
            )
        return EmbeddingSimilarityReranker(
            faiss_db=faiss_db,
            lambda_weight=float(cfg.get("reranker.embedding_similarity_lambda", 0.7)),
        )
    if strategy == "llm":
        from .llm_reranker import LLMReranker

        llm_client = deps.get("llm_client")
        if not adapter_contract(llm_client).supports(
            AdapterCapability.SYNC_TEXT_GENERATION
        ):
            return MMRReranker(
                float(cfg.get("reranker.mmr_lambda", 0.7)),
                degradation_reason_code="adapter_capability_unsupported",
            )
        return LLMReranker(
            llm_client=llm_client,
            batch_size=int(cfg.get("reranker.llm_batch_size", 10)),
            cost_control=deps.get("cost_control"),
        )
    if strategy == "hybrid":
        from .embedding_similarity_reranker import EmbeddingSimilarityReranker
        from .llm_reranker import LLMReranker

        faiss_db = deps.get("faiss_db")
        llm_client = deps.get("llm_client")
        if not adapter_contract(faiss_db).supports(
            AdapterCapability.VECTOR_ACCESS
        ) or not adapter_contract(llm_client).supports(
            AdapterCapability.SYNC_TEXT_GENERATION
        ):
            return MMRReranker(
                float(cfg.get("reranker.mmr_lambda", 0.7)),
                degradation_reason_code="adapter_capability_unsupported",
            )

        return HybridReranker(
            EmbeddingSimilarityReranker(
                faiss_db=faiss_db,
                lambda_weight=float(
                    cfg.get("reranker.embedding_similarity_lambda", 0.7)
                ),
            ),
            LLMReranker(
                llm_client=llm_client,
                batch_size=int(cfg.get("reranker.llm_batch_size", 10)),
                cost_control=deps.get("cost_control"),
            ),
        )
    # 未知或默认策略使用不依赖外部 Provider 的 MMR。
    mmr_lambda = float(cfg.get("reranker.mmr_lambda", 0.7))
    return MMRReranker(mmr_lambda)


class HybridReranker:
    """先以向量相似度缩小候选，再交给 LLM 精排。"""

    def __init__(self, embedding_reranker, llm_reranker) -> None:
        """保存向量重排器和 LLM 重排器。"""

        self._embedding = embedding_reranker
        self._llm = llm_reranker

    def rerank(
        self, results: list[HybridResult], k: int, **kwargs: Any
    ) -> list[HybridResult]:
        """先保留最多 ``3 * k`` 项，再执行 LLM 精排。"""

        narrowed = self._embedding.rerank(results, min(k * 3, len(results)), **kwargs)
        return self._llm.rerank(narrowed, k, **kwargs)


__all__ = ["create_reranker", "RerankerStrategy", "MMRReranker", "HybridReranker"]
