"""可插拔重排序策略工厂 — MMR / Cross-Encoder / LLM / Hybrid。"""

from __future__ import annotations

from typing import Any, Protocol

from .rrf_fusion import HybridResult


class RerankerStrategy(Protocol):
    def rerank(
        self, results: list[HybridResult], k: int, **kwargs: Any
    ) -> list[HybridResult]: ...


async def create_reranker(
    strategy: str, config: dict[str, Any] | None = None, **deps: Any
):
    cfg = config or {}
    if strategy == "cross_encoder":
        from .cross_encoder_reranker import CrossEncoderReranker

        return CrossEncoderReranker(
            faiss_db=deps.get("faiss_db"),
            lambda_weight=float(cfg.get("reranker.cross_encoder_lambda", 0.7)),
        )
    if strategy == "llm":
        from .llm_reranker import LLMReranker

        return LLMReranker(
            llm_client=deps.get("llm_client"),
            batch_size=int(cfg.get("reranker.llm_batch_size", 10)),
        )
    if strategy == "hybrid":
        from .cross_encoder_reranker import CrossEncoderReranker
        from .llm_reranker import LLMReranker

        return HybridReranker(
            CrossEncoderReranker(
                faiss_db=deps.get("faiss_db"),
                lambda_weight=float(cfg.get("reranker.cross_encoder_lambda", 0.7)),
            ),
            LLMReranker(
                llm_client=deps.get("llm_client"),
                batch_size=int(cfg.get("reranker.llm_batch_size", 10)),
            ),
        )
    # default: MMR
    mmr_lambda = float(cfg.get("reranker.mmr_lambda", 0.7))
    return MMRReranker(mmr_lambda)


class MMRReranker:
    def __init__(self, mmr_lambda: float = 0.7) -> None:
        self._lambda = mmr_lambda

    def rerank(
        self, results: list[HybridResult], k: int, **kwargs: Any
    ) -> list[HybridResult]:
        from .mmr_reranker import apply_mmr

        return apply_mmr(results, k, self._lambda)


class HybridReranker:
    def __init__(self, ce_reranker, llm_reranker) -> None:
        self._ce = ce_reranker
        self._llm = llm_reranker

    def rerank(
        self, results: list[HybridResult], k: int, **kwargs: Any
    ) -> list[HybridResult]:
        narrowed = self._ce.rerank(results, min(k * 3, len(results)), **kwargs)
        return self._llm.rerank(narrowed, k, **kwargs)


__all__ = ["create_reranker", "RerankerStrategy", "MMRReranker", "HybridReranker"]
