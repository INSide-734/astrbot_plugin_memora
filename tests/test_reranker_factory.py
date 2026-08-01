"""可插拔重排器策略工厂测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_result(doc_id: int, final_score: float, content: str = "") -> Any:
    """构造重排器工厂测试所需的最小检索结果。"""

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
        """构造显式声明文档向量访问能力的测试后端。"""

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
        """构造显式声明同步文本生成能力的测试客户端。"""

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
        """未知默认策略应创建不依赖外部能力的 MMR 重排器。"""
        from core.retrieval.reranker_factory import MMRReranker, create_reranker

        r = await create_reranker("default")
        assert isinstance(r, MMRReranker)

    @pytest.mark.asyncio
    async def test_create_mmr_explicit(self) -> None:
        """显式 ``mmr`` 策略应创建 MMR 重排器。"""
        from core.retrieval.reranker_factory import MMRReranker, create_reranker

        r = await create_reranker("mmr")
        assert isinstance(r, MMRReranker)

    @pytest.mark.asyncio
    async def test_create_embedding_similarity(self, faiss_db: MagicMock) -> None:
        """``embedding_similarity`` 应创建同名语义的重排器。"""
        from core.retrieval.embedding_similarity_reranker import (
            EmbeddingSimilarityReranker,
        )
        from core.retrieval.reranker_factory import create_reranker

        reranker = await create_reranker(
            "embedding_similarity",
            {"reranker.embedding_similarity_lambda": 0.7},
            deps={"faiss_db": faiss_db},
        )
        assert isinstance(reranker, EmbeddingSimilarityReranker)

    @pytest.mark.asyncio
    async def test_legacy_strategy_is_not_a_runtime_alias(
        self,
        faiss_db: MagicMock,
    ) -> None:
        """绕过配置迁移的旧策略值应安全回退，而不是形成长期双轨。"""

        from core.retrieval.reranker_factory import MMRReranker, create_reranker

        reranker = await create_reranker(
            "cross_encoder",
            deps={"faiss_db": faiss_db},
        )
        assert isinstance(reranker, MMRReranker)

    @pytest.mark.asyncio
    async def test_create_llm(self, llm_client: MagicMock) -> None:
        """具备同步生成能力时 ``llm`` 策略应创建 LLM 重排器。"""
        from core.retrieval.llm_reranker import LLMReranker
        from core.retrieval.reranker_factory import create_reranker

        r = await create_reranker("llm", deps={"llm_client": llm_client})
        assert isinstance(r, LLMReranker)

    @pytest.mark.asyncio
    async def test_create_hybrid(
        self, faiss_db: MagicMock, llm_client: MagicMock
    ) -> None:
        """两类外部能力都满足时应创建 Hybrid 重排器。"""
        from core.retrieval.reranker_factory import HybridReranker, create_reranker

        r = await create_reranker(
            "hybrid",
            deps={"faiss_db": faiss_db, "llm_client": llm_client},
        )
        assert isinstance(r, HybridReranker)

    def test_mmr_reranker_rerank(self) -> None:
        """MMR 包装器应返回不超过 ``k`` 项的重排结果。"""
        from core.retrieval.reranker_factory import MMRReranker

        r = MMRReranker(mmr_lambda=0.7)
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = r.rerank(results, k=3)
        assert len(output) == 3

    def test_hybrid_reranker_rerank(
        self, faiss_db: MagicMock, llm_client: MagicMock
    ) -> None:
        """Hybrid 应先执行 Embedding 相似度窄化，再保留 LLM 阶段。"""
        from core.retrieval.embedding_similarity_reranker import (
            EmbeddingSimilarityReranker,
        )
        from core.retrieval.llm_reranker import LLMReranker
        from core.retrieval.reranker_factory import HybridReranker

        llm_client.complete_sync.return_value = "[]"
        embedding = EmbeddingSimilarityReranker(faiss_db=None)
        llm = LLMReranker(llm_client=llm_client)
        hybrid = HybridReranker(embedding, llm)

        results = [_make_result(i, 0.9 - i * 0.05) for i in range(10)]
        output = hybrid._embedding.rerank(results, k=5, query="test")
        assert len(output) == 5
