"""最终重排与图距离消融的真实生效测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.adapter_capabilities import ASTRBOT_FAISS_CAPABILITIES
from core.retrieval.dual_route_retriever import DualRouteRetriever
from core.retrieval.graph_keyword_retriever import GraphKeywordResult
from core.retrieval.graph_retriever import GraphRetriever
from core.retrieval.graph_vector_retriever import GraphVectorResult
from core.retrieval.rrf_fusion import HybridResult, RRFFusion


def _candidate(doc_id: int, score: float, content: str) -> HybridResult:
    """构造最终重排测试使用的 canonical 候选。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata={"privacy_level": "shared"},
    )


def _ablation_engine(*, reranker: object) -> tuple[object, object]:
    """构造使用真实 DualRouteRetriever 的最小只读评测引擎。"""

    document = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                _candidate(1, 0.9, "第一条候选"),
                _candidate(2, 0.8, "第二条候选"),
            ]
        )
    )
    graph = SimpleNamespace(search=AsyncMock(return_value=[]))

    async def load_memory(_memory_id: int):
        """测试候选均含正文，不需要额外回填。"""

        return None

    config = {
        "reranker.mmr_lambda": 0.7,
        "reranker.embedding_similarity_lambda": 0.7,
        "graph_expansion_hops": 1,
        "memory_evolution": {"enabled": False, "mode": "disabled"},
    }
    dual = DualRouteRetriever(
        document,
        graph,
        load_memory,
        config,
        reranker=reranker,
    )
    faiss_db = SimpleNamespace(
        encode_query=MagicMock(return_value=[0.0, 1.0]),
        get_vector=MagicMock(
            side_effect=lambda doc_id: [1.0, 0.0] if doc_id == 1 else [0.0, 1.0]
        ),
        adapter_capabilities=ASTRBOT_FAISS_CAPABILITIES,
    )

    class Engine:
        """只把评测查询转交给当前实例的 DualRouteRetriever。"""

        async def search_memories(self, *, query: str, k: int = 5, **_kwargs):
            """执行一次隔离的双路召回。"""

            return await self.dual_route_retriever.search(query, k=k)

    engine = Engine()
    engine.config = config
    engine.hybrid_retriever = document
    engine.graph_keyword_retriever = SimpleNamespace(expansion_hops=1)
    engine.graph_retriever = graph
    engine.dual_route_retriever = dual
    engine.faiss_db = faiss_db
    return engine, reranker


@pytest.mark.asyncio
async def test_final_reranker_off_does_not_call_live_reranker() -> None:
    """关闭最终重排后必须按 baseline 分数排序，且不调用 live reranker。"""

    from core.evaluation.retrieval_ablation import RetrievalAblationController

    live_reranker = SimpleNamespace(rerank=MagicMock(side_effect=AssertionError))
    engine, _ = _ablation_engine(reranker=live_reranker)

    prepared = RetrievalAblationController(engine).prepare("final_reranker_off")
    results = await prepared.engine.search_memories(query="候选", k=1)

    assert prepared.available is True
    assert prepared.engine.dual_route_retriever.reranker is None
    assert [item.doc_id for item in results] == [1]
    live_reranker.rerank.assert_not_called()


@pytest.mark.asyncio
async def test_final_mmr_variant_invokes_mmr_reranker() -> None:
    """MMR 变体必须替换最终重排器，并在候选数大于 K 时实际调用。"""

    from core.evaluation.retrieval_ablation import RetrievalAblationController
    from core.retrieval.reranker_factory import MMRReranker

    engine, _ = _ablation_engine(reranker=SimpleNamespace(rerank=MagicMock()))
    prepared = RetrievalAblationController(engine).prepare("final_reranker_mmr")
    reranker = prepared.engine.dual_route_retriever.reranker
    original = reranker.rerank
    reranker.rerank = MagicMock(wraps=original)

    await prepared.engine.search_memories(query="候选", k=1)

    assert isinstance(reranker, MMRReranker)
    reranker.rerank.assert_called_once()
    assert prepared.effective_settings["final_reranker"] == "mmr"


@pytest.mark.asyncio
async def test_embedding_similarity_variant_uses_document_vectors() -> None:
    """向量相似度变体必须读取文档向量并能改变最终排序。"""

    from core.evaluation.retrieval_ablation import RetrievalAblationController

    engine, _ = _ablation_engine(reranker=SimpleNamespace(rerank=MagicMock()))
    prepared = RetrievalAblationController(engine).prepare(
        "final_reranker_embedding_similarity"
    )

    results = await prepared.engine.search_memories(query="候选", k=1)

    assert prepared.available is True
    assert [item.doc_id for item in results] == [2]
    assert prepared.engine.faiss_db.get_vector.call_count == 2
    assert prepared.effective_settings["final_reranker"] == "embedding_similarity"
    assert prepared.execution_reason_code() == "available"


@pytest.mark.asyncio
async def test_embedding_similarity_runtime_failure_is_not_reported_available() -> None:
    """文档向量运行时失效后，探针必须拒绝把 fallback 标记为完成。"""

    from core.evaluation.retrieval_ablation import RetrievalAblationController

    engine, _ = _ablation_engine(reranker=SimpleNamespace(rerank=MagicMock()))
    engine.faiss_db.get_vector.side_effect = RuntimeError("vector-secret-canary")
    prepared = RetrievalAblationController(engine).prepare(
        "final_reranker_embedding_similarity"
    )

    await prepared.engine.search_memories(query="候选", k=1)

    assert prepared.execution_reason_code() == "missing_document_vector_access"


@pytest.mark.asyncio
async def test_graph_retriever_exposes_numeric_minimum_distance() -> None:
    """图关键词候选的最小 hop 必须进入内部数值评分明细。"""

    keyword = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                GraphKeywordResult(
                    doc_id=17,
                    score=0.8,
                    content="二跳事实",
                    metadata={"importance": 0.5},
                    graph_distance=2,
                )
            ]
        )
    )
    vector = SimpleNamespace(search=AsyncMock(return_value=[]))
    retriever = GraphRetriever(keyword, vector, RRFFusion(), config={})

    results = await retriever.search("事实", k=1)

    assert len(results) == 1
    assert results[0].score_breakdown is not None
    assert results[0].score_breakdown["graph_min_distance"] == 2.0
    assert "graph_min_distance" not in results[0].metadata


@pytest.mark.asyncio
async def test_vector_only_graph_result_does_not_invent_distance() -> None:
    """只有图向量证据的候选不得伪造关键词路径 hop。"""

    keyword = SimpleNamespace(search=AsyncMock(return_value=[]))
    vector = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                GraphVectorResult(
                    doc_id=23,
                    score=0.9,
                    content="向量事实",
                    metadata={"importance": 0.5},
                )
            ]
        )
    )
    retriever = GraphRetriever(keyword, vector, RRFFusion(), config={})

    results = await retriever.search("事实", k=1)

    assert len(results) == 1
    assert results[0].score_breakdown is not None
    assert "graph_min_distance" not in results[0].score_breakdown
    assert "graph_min_distance" not in results[0].metadata
