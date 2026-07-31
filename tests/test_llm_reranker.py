"""LLMReranker 测试 — 基于LLM的结果重排序。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.base.cost_control import CostControl
from core.base.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope


def _make_result(doc_id: int, final_score: float, content: str = "") -> Any:
    """构造仅包含重排所需字段的候选结果。"""

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


class TestLLMReranker:
    @pytest.fixture
    def llm_client(self) -> MagicMock:
        """构造同步 Provider 替身。"""

        client = MagicMock()
        client.complete_sync = MagicMock()
        return client

    @pytest.fixture
    def reranker(self, llm_client: MagicMock) -> Any:
        """构造允许请求级 LLM 重排的质量档实例。"""

        from core.retrieval.llm_reranker import LLMReranker

        return LLMReranker(
            llm_client=llm_client,
            batch_size=10,
            cost_control=CostControl(
                mode="quality",
                max_extra_llm_calls_per_turn=1,
                llm_reranker_min_candidates=1,
            ),
        )

    @pytest.mark.asyncio
    async def test_rerank_no_llm_client(self) -> None:
        """没有 LLM 客户端时按输入顺序返回前 k 项。"""
        from core.retrieval.llm_reranker import LLMReranker

        reranker = LLMReranker(llm_client=None)
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = await reranker.rerank(results, k=3, query="test")
        assert len(output) == 3

    @pytest.mark.asyncio
    async def test_rerank_short_circuit(self, reranker: Any) -> None:
        """候选数量不超过 k 时不调用 Provider。"""
        results = [_make_result(1, 0.9), _make_result(2, 0.8)]
        output = await reranker.rerank(results, k=3, query="test")
        assert len(output) == 2

    @pytest.mark.asyncio
    async def test_rerank_empty_query(self, reranker: Any) -> None:
        """空查询直接跳过重排。"""
        results = [_make_result(i, 0.9 - i * 0.1) for i in range(5)]
        output = await reranker.rerank(results, k=3, query="")
        assert len(output) == 3

    @pytest.mark.asyncio
    async def test_rerank_with_valid_llm_response(
        self, reranker: Any, llm_client: MagicMock
    ) -> None:
        """有效 LLM 分数应与原分数融合并提交额度。"""

        llm_client.complete_sync.return_value = "[9.0, 5.0, 3.0, 1.0]"
        results = [_make_result(i, 0.8 - i * 0.1) for i in range(4)]
        budget = ExtraLlmBudget(max_calls=1)
        with extra_llm_budget_scope(budget):
            output = await reranker.rerank(results, k=3, query="test query")
        assert len(output) == 3
        # 分数按 (原分数 + LLM 分数 / 10) / 2 融合后降序排列。
        for i in range(len(output) - 1):
            assert output[i].final_score >= output[i + 1].final_score
        assert budget.snapshot().used == 1

    @pytest.mark.asyncio
    async def test_rerank_llm_returns_invalid_json(
        self, reranker: Any, llm_client: MagicMock
    ) -> None:
        """Provider 成功但响应无效时提交额度并保持输入不变。"""

        llm_client.complete_sync.return_value = "invalid response without array"
        results = [_make_result(1, 0.2), _make_result(2, 0.9), _make_result(3, 0.4)]
        original_scores = [result.final_score for result in results]
        budget = ExtraLlmBudget(max_calls=1)
        with extra_llm_budget_scope(budget):
            output = await reranker.rerank(results, k=2, query="test")
        assert output == results[:2]
        assert [result.final_score for result in results] == original_scores
        assert budget.snapshot().used == 1

    @pytest.mark.asyncio
    async def test_rerank_llm_raises_exception(
        self, reranker: Any, llm_client: MagicMock
    ) -> None:
        """Provider 普通失败时释放 reservation 并保持输入不变。"""

        llm_client.complete_sync.side_effect = RuntimeError("LLM unavailable")
        results = [_make_result(1, 0.2), _make_result(2, 0.9), _make_result(3, 0.4)]
        original_scores = [result.final_score for result in results]
        budget = ExtraLlmBudget(max_calls=1)
        with extra_llm_budget_scope(budget):
            output = await reranker.rerank(results, k=2, query="test")
        assert output == results[:2]
        assert [result.final_score for result in results] == original_scores
        assert budget.snapshot().used == 0
        assert budget.snapshot().reserved == 0

    @pytest.mark.asyncio
    async def test_budget_denial_preserves_input_order_and_scores(
        self,
        reranker: Any,
        llm_client: MagicMock,
    ) -> None:
        """额度拒绝不得修改候选顺序、分数或调用 Provider。"""

        results = [_make_result(1, 0.2), _make_result(2, 0.9), _make_result(3, 0.4)]
        original_scores = [result.final_score for result in results]
        with extra_llm_budget_scope(ExtraLlmBudget(max_calls=0)):
            output = await reranker.rerank(results, k=2, query="test")
        assert output == results[:2]
        assert [result.final_score for result in results] == original_scores
        llm_client.complete_sync.assert_not_called()
