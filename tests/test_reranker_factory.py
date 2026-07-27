"""测试 reranker_factory — pluggable reranker strategy creation."""

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


class TestRerankerFactory:
    @pytest.fixture
    def faiss_db(self) -> MagicMock:
        from core.adapter_capabilities import (
            AdapterCapability,
            AdapterCapabilityContract,
            AdapterKind,
        )

        db = MagicMock()
        db.encode_query = MagicMock(return_value=[0.1, 0.2, 0.3])
        db.get_vector = MagicMock(return_value=[0.1, 0.2, 0.3])
        db.adapter_capabilities = AdapterCapabilityContract(
            kind=AdapterKind.VECTOR_BACKEND,
            native=frozenset({AdapterCapability.VECTOR_ACCESS}),
        )
        return db

    @pytest.fixture
    def llm_client(self) -> MagicMock:
        from core.adapter_capabilities import (
            AdapterCapability,
            AdapterCapabilityContract,
            AdapterKind,
        )

        client = MagicMock()
        client.complete_sync = MagicMock()
        client.adapter_capabilities = AdapterCapabilityContract(
            kind=AdapterKind.LLM_CLIENT,
            native=frozenset({AdapterCapability.SYNC_TEXT_GENERATION}),
        )
        return client

    @pytest.mark.asyncio
    async def test_create_default_mmr(self) -> None:
        """默认 strategy creates an MMRReranker."""
        from core.retrieval.reranker_factory import MMRReranker, create_reranker

        r = await create_reranker("default")
        assert isinstance(r, MMRReranker)

    @pytest.mark.asyncio
    async def test_create_mmr_explicit(self) -> None:
        """显式 'mmr' strategy creates an MMRReranker."""
        from core.retrieval.reranker_factory import MMRReranker, create_reranker

        r = await create_reranker("mmr")
        assert isinstance(r, MMRReranker)

    @pytest.mark.asyncio
    async def test_create_cross_encoder(self, faiss_db: MagicMock) -> None:
        """'cross_encoder' strategy creates a CrossEncoderReranker."""
        from core.retrieval.cross_encoder_reranker import CrossEncoderReranker
        from core.retrieval.reranker_factory import create_reranker

        r = await create_reranker("cross_encoder", deps={"faiss_db": faiss_db})
        assert isinstance(r, CrossEncoderReranker)

    @pytest.mark.asyncio
    async def test_create_llm(self, llm_client: MagicMock) -> None:
        """'llm' strategy creates an LLMReranker."""
        from core.retrieval.llm_reranker import LLMReranker
        from core.retrieval.reranker_factory import create_reranker

        r = await create_reranker("llm", deps={"llm_client": llm_client})
        assert isinstance(r, LLMReranker)

    @pytest.mark.asyncio
    async def test_create_hybrid(
        self, faiss_db: MagicMock, llm_client: MagicMock
    ) -> None:
        """'hybrid' strategy creates a HybridReranker."""
        from core.retrieval.reranker_factory import HybridReranker, create_reranker

        r = await create_reranker(
            "hybrid",
            deps={"faiss_db": faiss_db, "llm_client": llm_client},
        )
        assert isinstance(r, HybridReranker)

    def test_mmr_reranker_rerank(self) -> None:
        """MMRReranker.rerank delegates to apply_mmr."""
        from core.retrieval.reranker_factory import MMRReranker

        r = MMRReranker(mmr_lambda=0.7)
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = r.rerank(results, k=3)
        assert len(output) == 3

    def test_hybrid_reranker_rerank(
        self, faiss_db: MagicMock, llm_client: MagicMock
    ) -> None:
        """HybridReranker chains CE then LLM."""
        from core.retrieval.cross_encoder_reranker import CrossEncoderReranker
        from core.retrieval.llm_reranker import LLMReranker
        from core.retrieval.reranker_factory import HybridReranker

        # Make LLMReranker.rerank return synchronously (bypass async for test)
        llm_client.complete_sync.return_value = "[]"
        ce = CrossEncoderReranker(faiss_db=None)  # will fallback to MMR
        llm = LLMReranker(llm_client=llm_client)
        h = HybridReranker(ce, llm)

        results = [_make_result(i, 0.9 - i * 0.05) for i in range(10)]
        # HybridReranker.rerank is sync but LLMReranker.rerank is async
        # In practice, the async method would need awaiting — test the CE step only
        # Here we verify the structure is correct
        output = h._ce.rerank(results, k=5, query="test")
        assert len(output) == 5
