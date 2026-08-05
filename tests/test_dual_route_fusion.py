"""双路召回融合职责拆分的行为与结构回归。"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from core.models.recall_strategy import RecallStrategy
from core.retrieval.dual_route_fusion import (
    compute_strategy_weights,
    merge_dual_results,
    route_weights_for_query,
)
from core.retrieval.dual_route_retriever import DualRouteRetriever
from core.retrieval.graph_retriever import GraphResult
from core.retrieval.rrf_fusion import HybridResult


def _document_result(doc_id: int, score: float) -> HybridResult:
    """构造带完整正文和元数据的文档路候选。"""

    return HybridResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score / 10,
        bm25_score=score / 2,
        vector_score=score / 3,
        content=f"document-{doc_id}",
        metadata={"source": "document"},
        score_breakdown={"document_internal": score},
    )


def _graph_result(doc_id: int, score: float) -> GraphResult:
    """构造图路候选，并保留图内部分数证据。"""

    return GraphResult(
        doc_id=doc_id,
        final_score=score,
        rrf_score=score / 10,
        keyword_score=score / 2,
        vector_score=score / 3,
        content="",
        metadata={},
        score_breakdown={"graph_internal": score},
    )


@pytest.mark.asyncio
async def test_merge_preserves_order_breakdown_and_missing_document_loading() -> None:
    """抽离后的融合器必须保持排序、解释字段和缺失正文回填语义。"""

    loader = AsyncMock(
        side_effect=lambda doc_id: {
            "text": f"loaded-{doc_id}",
            "metadata": {"source": "loader"},
        }
    )
    results = await merge_dual_results(
        [_document_result(1, 0.8)],
        [_graph_result(1, 0.6), _graph_result(2, 0.9)],
        "关系查询",
        memory_loader=loader,
        document_route_weight=0.65,
        graph_route_weight=0.35,
        cross_route_bonus=0.08,
        dynamic_route_weighting=False,
        atom_route_weight=0.25,
    )

    assert [result.doc_id for result in results] == [1, 2]
    assert results[0].content == "document-1"
    assert results[1].content == "loaded-2"
    assert results[0].score_breakdown == {
        "document_internal": 0.8,
        "graph_internal": 0.6,
        "document_route_score": 1.0,
        "graph_route_score": 0.6667,
        "document_route_weight": 0.65,
        "graph_route_weight": 0.35,
        "cross_route_bonus": 0.08,
        "dual_route_final_score": 0.9633,
        "query_intent": "fixed",
        "document_keyword_score": 0.4,
        "document_vector_score": 0.2667,
        "graph_keyword_score": 0.3,
        "graph_vector_score": 0.2,
    }
    loader.assert_awaited_once_with(2)


def test_route_and_strategy_weights_preserve_existing_contract() -> None:
    """职责拆分不得改变关键词路由和显式策略的权重契约。"""

    assert route_weights_for_query(
        "他是谁",
        document_route_weight=0.65,
        graph_route_weight=0.35,
        dynamic_route_weighting=True,
    ) == (0.45, 0.55, "relationship")
    assert compute_strategy_weights(RecallStrategy.RELATIONSHIP_REVIEW) == (0.30, 0.70)


@pytest.mark.asyncio
async def test_retriever_delegate_propagates_loader_cancellation() -> None:
    """兼容委托边界不得把 canonical 回填任务的取消降级为空结果。"""

    loader = AsyncMock(side_effect=asyncio.CancelledError)
    retriever = DualRouteRetriever(AsyncMock(), AsyncMock(), loader)

    with pytest.raises(asyncio.CancelledError):
        await retriever._merge_dual_results(
            [],
            [_graph_result(2, 0.9)],
            "关系查询",
        )


def test_retriever_public_entry_stays_stable_and_module_is_bounded() -> None:
    """公开检索器入口保持不变，编排模块物理行数不得再次超过上限。"""

    assert DualRouteRetriever.__module__ == "core.retrieval.dual_route_retriever"
    source_file = inspect.getsourcefile(DualRouteRetriever)
    assert source_file is not None
    with open(source_file, encoding="utf-8") as source:
        assert sum(1 for _line in source) <= 800
