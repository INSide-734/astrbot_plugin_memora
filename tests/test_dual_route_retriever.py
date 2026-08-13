"""DualRouteRetriever 测试 — 文档+图双路检索融合。"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_hybrid(
    doc_id: int,
    final_score: float,
    content: str = "",
    metadata: dict | None = None,
    bm25: float | None = None,
    vector: float | None = None,
    breakdown: dict | None = None,
) -> Any:
    from core.features.retrieval.rrf_fusion import HybridResult

    return HybridResult(
        doc_id=doc_id,
        final_score=final_score,
        rrf_score=final_score,
        bm25_score=bm25,
        vector_score=vector,
        content=content or f"content_{doc_id}",
        metadata=metadata or {},
        score_breakdown=breakdown,
    )


def _make_graph(
    doc_id: int,
    final_score: float,
    content: str = "",
    metadata: dict | None = None,
    kw_score: float | None = None,
    vec_score: float | None = None,
    breakdown: dict | None = None,
) -> Any:
    from core.features.retrieval.graph_retriever import GraphResult

    return GraphResult(
        doc_id=doc_id,
        final_score=final_score,
        rrf_score=final_score,
        keyword_score=kw_score,
        vector_score=vec_score,
        content=content or f"content_{doc_id}",
        metadata=metadata or {},
        score_breakdown=breakdown,
    )


class TestDualRouteRetriever:
    @pytest.fixture
    def doc_retriever(self) -> AsyncMock:
        dr = AsyncMock()
        dr.search = AsyncMock(return_value=[])
        return dr

    @pytest.fixture
    def graph_retriever(self) -> AsyncMock:
        gr = AsyncMock()
        gr.search = AsyncMock(return_value=[])
        return gr

    @pytest.fixture
    def memory_loader(self) -> AsyncMock:
        ml = AsyncMock(return_value=None)
        return ml

    @pytest.fixture
    def retriever(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> Any:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        return DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
        )

    @pytest.mark.asyncio
    async def test_search_doc_only(
        self, retriever: Any, doc_retriever: AsyncMock
    ) -> None:
        """仅文档路返回结果时，应原样返回这些结果。"""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "doc result"),
        ]
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1

    @pytest.mark.asyncio
    async def test_search_applies_derived_expansion_before_return(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        direct = _make_hybrid(1, 0.9, "直接证据")
        derived = _make_hybrid(2, 0.7, "一跳证据")
        doc_retriever.search.return_value = [direct]
        expander = MagicMock()
        expander.expand = AsyncMock(return_value=[direct, derived])
        retriever = DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
            config={
                "memory_evolution": {
                    "enabled": True,
                    "mode": "readonly",
                    "max_query_expansions": 4,
                    "projection_budget_chars": 500,
                }
            },
            derived_expander=expander,
        )

        results = await retriever.search(
            "测试",
            k=5,
            session_id="private:user-a",
        )

        assert [item.doc_id for item in results] == [1, 2]

    @pytest.mark.asyncio
    async def test_search_keeps_baseline_when_derived_expansion_fails(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        direct = _make_hybrid(1, 0.9, "直接证据")
        doc_retriever.search.return_value = [direct]
        expander = MagicMock()
        expander.expand = AsyncMock(side_effect=RuntimeError("扩展失败"))
        retriever = DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
            config={"memory_evolution": {"enabled": True, "mode": "readonly"}},
            derived_expander=expander,
        )

        results = await retriever.search(
            "测试",
            k=5,
            session_id="private:user-a",
        )

        assert [item.doc_id for item in results] == [1]

    @pytest.mark.asyncio
    async def test_search_does_not_expand_in_disabled_mode(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        direct = _make_hybrid(1, 0.9, "直接证据")
        doc_retriever.search.return_value = [direct]
        expander = MagicMock()
        expander.expand = AsyncMock(
            side_effect=AssertionError("禁用模式不应调用扩展器")
        )
        retriever = DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
            config={"memory_evolution": {"enabled": False, "mode": "disabled"}},
            derived_expander=expander,
        )

        results = await retriever.search("测试", k=5)

        assert [item.doc_id for item in results] == [1]

    @pytest.mark.asyncio
    async def test_search_does_not_expose_shadow_candidates(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        direct = _make_hybrid(1, 0.9, "直接证据")
        doc_retriever.search.return_value = [direct]
        expander = MagicMock()
        expander.expand = AsyncMock(
            side_effect=AssertionError("观察模式不应进入回答上下文")
        )
        retriever = DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
            config={"memory_evolution": {"enabled": True, "mode": "shadow"}},
            derived_expander=expander,
        )

        results = await retriever.search("测试", k=5)

        assert [item.doc_id for item in results] == [1]
        expander.expand.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_projection_reader_runs_before_reranker(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        direct = _make_hybrid(1, 0.9, "直接证据")
        attached = _make_hybrid(
            1,
            0.9,
            "直接证据",
            {
                "derived_projections": [
                    {"type": "episode_summary", "summary": "摘要", "confidence": 0.8}
                ]
            },
        )
        relation_result = _make_hybrid(2, 0.7, "关系证据")
        doc_retriever.search.return_value = [direct]
        projection_reader = MagicMock()
        projection_reader.attach = AsyncMock(return_value=[attached, relation_result])
        order: list[str] = []

        async def rerank(values, k, *, query):
            order.append("rerank")
            assert values[0].metadata["derived_projections"]
            return values

        reranker = MagicMock()
        reranker.rerank = rerank
        retriever = DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
            config={"memory_evolution": {"enabled": True, "mode": "readonly"}},
            reranker=reranker,
            projection_reader=projection_reader,
        )

        results = await retriever.search("查询", k=5, session_id="private:user-a")

        assert [item.doc_id for item in results] == [1, 2]
        projection_reader.attach.assert_awaited_once()
        assert order == ["rerank"]

    @pytest.mark.asyncio
    async def test_projection_reader_is_not_called_in_shadow_mode(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        direct = _make_hybrid(1, 0.9, "直接证据")
        doc_retriever.search.return_value = [direct]
        projection_reader = MagicMock()
        projection_reader.attach = AsyncMock(
            side_effect=AssertionError("观察模式不应读取 projection")
        )
        retriever = DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
            config={"memory_evolution": {"enabled": True, "mode": "shadow"}},
            projection_reader=projection_reader,
        )

        results = await retriever.search("查询", k=5, session_id="private:user-a")

        assert [item.doc_id for item in results] == [1]
        projection_reader.attach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_both_routes(
        self,
        retriever: Any,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """双路均有结果时应合并；仅图路命中的文档需要通过 memory_loader 回填。"""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "doc result"),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "graph result"),
        ]
        # 文档路结果自带正文；仅图路结果需要由 memory_loader 回填正文。
        memory_loader.return_value = {"text": "graph result loaded", "metadata": {}}

        results = await retriever.search("test", k=5)
        assert len(results) == 2
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 in doc_ids

    @pytest.mark.asyncio
    async def test_search_cross_route_bonus(
        self, retriever: Any, doc_retriever: AsyncMock, graph_retriever: AsyncMock
    ) -> None:
        """同一 doc_id 同时命中两路时应获得跨路加分。"""
        shared_meta = {"importance": 0.7}
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.8, "shared content", shared_meta, bm25=0.7, vector=0.6),
        ]
        graph_retriever.search.return_value = [
            _make_graph(
                1, 0.7, "shared content", shared_meta, kw_score=0.6, vec_score=0.5
            ),
        ]
        # 两路命中同一 doc_id 时，文档路结果提供正文和非空 metadata。
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1

    @pytest.mark.asyncio
    async def test_privacy_filter_group_chat(
        self, retriever: Any, doc_retriever: AsyncMock
    ) -> None:
        """群聊应过滤 confidential 记忆。"""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "public", {"privacy_level": "shared"}),
            _make_hybrid(2, 0.8, "secret", {"privacy_level": "confidential"}),
        ]
        results = await retriever.search("test", k=10, chat_type="group")
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 not in doc_ids  # confidential 记忆已过滤

    @pytest.mark.asyncio
    async def test_privacy_filter_private_chat(
        self, retriever: Any, doc_retriever: AsyncMock
    ) -> None:
        """私聊不应过滤 confidential 记忆。"""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "public", {"privacy_level": "shared"}),
            _make_hybrid(2, 0.8, "secret", {"privacy_level": "confidential"}),
        ]
        results = await retriever.search("test", k=10, chat_type="private")
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 in doc_ids  # 私聊允许 confidential 记忆

    def test_route_weights_for_relation_query(self, retriever: Any) -> None:
        """关系关键词应提高图路权重。"""
        doc_w, graph_w, intent = retriever._route_weights_for_query("他是谁")
        assert graph_w > doc_w
        assert "relationship" in intent

    def test_route_weights_for_factual_query(self, retriever: Any) -> None:
        """事实关键词应提高文档路权重。"""
        doc_w, graph_w, intent = retriever._route_weights_for_query("这是什么东西")
        assert doc_w > graph_w
        assert "factual" in intent

    def test_route_weights_for_temporal_query(self, retriever: Any) -> None:
        """时间关键词应提高图路权重。"""
        doc_w, graph_w, intent = retriever._route_weights_for_query("昨天发生了什么")
        assert "temporal" in intent

    def test_route_weights_dynamic_disabled(self, retriever: Any) -> None:
        """禁用动态路由权重时应使用固定权重。"""
        retriever.dynamic_route_weighting = False
        doc_w, graph_w, intent = retriever._route_weights_for_query("他是谁")
        assert intent == "fixed"
        assert doc_w == retriever.document_route_weight
        assert graph_w == retriever.graph_route_weight

    @pytest.mark.parametrize(
        "strategy,expected_doc,expected_graph",
        [
            ("contextual_similarity", 0.70, 0.30),
            ("topic_association", 0.65, 0.35),
            ("preference_query", 0.80, 0.20),
            ("relationship_review", 0.30, 0.70),
        ],
    )
    def test_strategy_weights(
        self, retriever: Any, strategy: str, expected_doc: float, expected_graph: float
    ) -> None:
        """每个 RecallStrategy 都应映射到预定义的文档/图路权重。"""
        from core.models.recall_strategy import RecallStrategy

        s = RecallStrategy(strategy)
        doc_w, graph_w = retriever._compute_strategy_weights(s)
        assert doc_w == expected_doc
        assert graph_w == expected_graph

    @pytest.mark.asyncio
    async def test_search_with_query_intent(
        self,
        retriever: Any,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """query_intent 应覆盖基于关键词的权重调整。"""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "content"),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "graph"),
        ]
        memory_loader.return_value = {"text": "graph content loaded", "metadata": {}}

        class _FakeIntent:
            intent = "relationship"

        results = await retriever.search("query", k=5, query_intent=_FakeIntent())
        assert len(results) >= 1

    def test_filter_by_privacy_empty_metadata(self, retriever: Any) -> None:
        """缺少 privacy_level 的记忆应按 shared 处理。"""
        results = [
            _make_hybrid(1, 0.9, "no metadata", {}),
        ]
        filtered = retriever._filter_by_privacy(results, "group")
        assert len(filtered) == 1

    # ── 边界与未覆盖路径测试 ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_graph_only_no_doc_results_uses_graph_fallback(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """没有文档路结果而有图路结果时，应使用图路作为降级结果。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = []
        graph_retriever.search.return_value = [
            _make_graph(
                1,
                0.8,
                "",
                {},
                kw_score=0.7,
                vec_score=0.6,
                breakdown={"graph_raw": 0.8},
            ),
        ]
        memory_loader.return_value = {
            "text": "graph only loaded",
            "metadata": {"privacy_level": "shared", "source": "graph"},
        }
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1
        assert results[0].content == "graph only loaded"
        assert results[0].metadata["source"] == "graph"
        assert results[0].score_breakdown["document_route_score"] == 0.0
        assert results[0].score_breakdown["graph_route_score"] == 1.0
        assert "graph_keyword_score" in results[0].score_breakdown
        memory_loader.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_persona_boost_enabled(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """启用 persona_interpretation 时，匹配当前人格的记忆应获得加分。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(
                1,
                0.5,
                "test",
                {
                    "persona_interpretations": {"p1": "解读1"},
                },
            ),
        ]
        graph_retriever.search.return_value = []
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            config={
                "persona_interpretation.enabled": True,
                "persona_interpretation.boost": 1.5,
            },
        )
        results = await retriever.search("test", k=5, persona_id="p1")
        assert len(results) >= 1
        # final_score 应加分并限制在 1.0 以内。
        assert results[0].final_score <= 1.0

    @pytest.mark.asyncio
    async def test_persona_boost_disabled_by_config(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """禁用 persona_interpretation 时不应加分。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(
                1,
                0.5,
                "test",
                {
                    "persona_interpretations": {"p1": "解读1"},
                },
            ),
        ]
        graph_retriever.search.return_value = []
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            config={"persona_interpretation.enabled": False},
        )
        results = await retriever.search("test", k=5, persona_id="p1")
        assert len(results) >= 1
        assert results[0].final_score == 0.5  # unchanged

    @pytest.mark.asyncio
    async def test_personalized_ranking_path(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """提供 user_id、ranker 和 profile_manager 时应调用个性化排序器。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.8, "test content"),
        ]
        graph_retriever.search.return_value = []
        mock_ranker = MagicMock()
        mock_ranker.apply = MagicMock(return_value=doc_retriever.search.return_value)
        mock_profile = MagicMock()
        mock_profile.get_tag_weights = AsyncMock(return_value={"tag1": 1.0})
        mock_profile.get_profile = AsyncMock(return_value={"interests": ["coding"]})
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            personalized_ranker=mock_ranker,
            profile_manager=mock_profile,
        )
        results = await retriever.search("test", k=5, user_id="user1")
        assert len(results) >= 1
        mock_ranker.apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_personalized_ranking_suppresses_exception(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """个性化排序失败不应中断检索。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.8, "test"),
        ]
        graph_retriever.search.return_value = []
        mock_profile = MagicMock()
        mock_profile.get_tag_weights = AsyncMock(side_effect=RuntimeError("db down"))
        mock_profile.get_profile = AsyncMock(return_value={})
        mock_ranker = MagicMock()
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            personalized_ranker=mock_ranker,
            profile_manager=mock_profile,
        )
        results = await retriever.search("test", k=5, user_id="user1")
        assert len(results) == 1  # still returns results

    @pytest.mark.asyncio
    async def test_reranker_path(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """存在重排序器且结果多于一条时应调用它。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "result 1"),
            _make_hybrid(2, 0.5, "result 2"),
        ]
        graph_retriever.search.return_value = []
        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(return_value=doc_retriever.search.return_value)
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            reranker=mock_reranker,
        )
        results = await retriever.search("test", k=5)
        mock_reranker.rerank.assert_called_once()
        assert results == doc_retriever.search.return_value

    @pytest.mark.asyncio
    async def test_async_reranker_is_awaited_and_receives_query(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """异步重排序器（包括 LLM 重排序器）应携带 query 等待完成。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.5, "less relevant"),
            _make_hybrid(2, 0.9, "more relevant"),
        ]
        graph_retriever.search.return_value = []

        class _AsyncReranker:
            def __init__(self) -> None:
                self.query = None

            async def rerank(
                self, results: list[Any], k: int, **kwargs: Any
            ) -> list[Any]:
                self.query = kwargs.get("query")
                return list(reversed(results))[:k]

        reranker = _AsyncReranker()
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            reranker=reranker,
        )

        results = await retriever.search("needle query", k=2)

        assert [r.doc_id for r in results] == [2, 1]
        assert reranker.query == "needle query"

    @pytest.mark.asyncio
    async def test_async_reranker_failure_falls_back_to_score_sort(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """异步重排序器等待后抛出异常时不应中断检索。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.2, "low"),
            _make_hybrid(2, 0.8, "high"),
        ]
        graph_retriever.search.return_value = []

        class _FailingAsyncReranker:
            async def rerank(
                self, results: list[Any], k: int, **kwargs: Any
            ) -> list[Any]:
                raise RuntimeError("llm unavailable")

        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            reranker=_FailingAsyncReranker(),
        )

        results = await retriever.search("test", k=2)

        assert [r.doc_id for r in results] == [2, 1]

    @pytest.mark.asyncio
    async def test_reranker_suppresses_exception(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """重排序失败不应中断检索。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "r1"),
            _make_hybrid(2, 0.5, "r2"),
        ]
        graph_retriever.search.return_value = []
        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(side_effect=RuntimeError("reranker down"))
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            reranker=mock_reranker,
        )
        results = await retriever.search("test", k=5)
        # 异常时回退到按 final_score 排序。
        assert len(results) == 2
        assert results[0].final_score >= results[1].final_score

    @pytest.mark.asyncio
    async def test_memory_loader_exception_yields_none(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """memory_loader 抛错时，doc_id 应因 loaded=None 被跳过。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "ok", {"importance": 0.5}),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "", {}),  # 没有正文和元数据，需要通过 loader 回填。
        ]
        memory_loader.side_effect = RuntimeError("load failure")
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search("test", k=5)
        # doc_id=1 应保留（有正文和 metadata）；doc_id=2 应跳过。
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids

    @pytest.mark.asyncio
    async def test_memory_loader_returns_none_skips_doc(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """memory_loader 返回 None 时应跳过该文档。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "ok", {"importance": 0.5}),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "", {}),  # 没有正文和元数据。
        ]
        memory_loader.return_value = None
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search("test", k=5)
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 not in doc_ids  # 已跳过该文档。

    # ── LLM intent 路由测试 ───────────────────────────────────────────

    def test_llm_intent_relationship(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """LLM intent=relationship 时应提高图路权重。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "relationship"

        doc_w, graph_w, intent = retriever._route_weights_for_query(
            "query", query_intent=_Intent()
        )
        assert graph_w > doc_w
        assert intent == "llm:relationship"
        assert doc_w == max(0.15, base_doc - 0.25)
        assert graph_w == min(0.85, base_graph + 0.25)

    def test_llm_intent_temporal(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """LLM intent=temporal 时应略微提高图路权重。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "temporal"

        doc_w, graph_w, intent = retriever._route_weights_for_query(
            "q", query_intent=_Intent()
        )
        assert intent == "llm:temporal"
        assert doc_w == max(0.15, base_doc - 0.15)
        assert graph_w == min(0.85, base_graph + 0.15)

    def test_llm_intent_factual(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """LLM intent=factual 时应提高文档路权重。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "factual"

        doc_w, graph_w, intent = retriever._route_weights_for_query(
            "q", query_intent=_Intent()
        )
        assert intent == "llm:factual"
        assert doc_w == min(0.9, base_doc + 0.2)
        assert graph_w == max(0.1, base_graph - 0.2)

    def test_llm_intent_preference(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """LLM intent=preference 时应略微提高文档路权重。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "preference"

        doc_w, graph_w, intent = retriever._route_weights_for_query(
            "q", query_intent=_Intent()
        )
        assert intent == "llm:preference"
        assert doc_w == min(0.9, base_doc + 0.1)
        assert graph_w == max(0.1, base_graph - 0.1)

    def test_llm_intent_default_no_override(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """LLM intent=default 时回退到关键词匹配。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        class _Intent:
            intent = "default"

        doc_w, graph_w, intent = retriever._route_weights_for_query(
            "random text", query_intent=_Intent()
        )
        # 不应添加 "llm:" 前缀。
        assert not intent.startswith("llm:")

    def test_zero_total_weight_fallback(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """总权重小于等于 0 时应回退到固定基础权重。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            config={"document_route_weight": 0.0, "graph_route_weight": 0.0},
        )
        # 权重为 0 时归一化会产生 0/0，但关键词命中会调整权重。
        # 强制关闭动态权重，直接测试总权重小于等于 0 的回退。
        retriever.dynamic_route_weighting = False
        doc_w, graph_w, intent = retriever._route_weights_for_query("random text")
        assert intent == "fixed"
        assert doc_w == 0.0
        assert graph_w == 0.0

    # ── _build_score_breakdown 覆盖测试 ───────────────────────────────

    def test_build_score_breakdown_with_graph_breakdown(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """_build_score_breakdown 应包含 graph_result 的 score_breakdown。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        doc = _make_hybrid(
            1, 0.8, "doc", {}, bm25=0.7, vector=0.6, breakdown={"doc_key": 0.5}
        )
        graph = _make_graph(
            1,
            0.7,
            "graph",
            {},
            kw_score=0.6,
            vec_score=0.5,
            breakdown={"graph_key": 0.4},
        )
        breakdown = retriever._build_score_breakdown(
            doc_result=doc,
            graph_result=graph,
            doc_signal=0.8,
            graph_signal=0.7,
            document_weight=0.65,
            graph_weight=0.35,
            route_bonus=0.08,
            final_score=0.85,
            intent="test_intent",
        )
        assert "doc_key" in breakdown
        assert "graph_key" in breakdown
        assert breakdown["query_intent"] == "test_intent"
        assert "document_route_score" in breakdown
        assert "graph_route_score" in breakdown

    @pytest.mark.asyncio
    async def test_search_with_strategy_triggers_compute_weights(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """向 search() 传入 strategy 时应触发 _compute_strategy_weights。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever
        from core.models.recall_strategy import RecallStrategy

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "content", {"importance": 0.5}),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "", {}),
        ]
        memory_loader.return_value = {"text": "loaded", "metadata": {"importance": 0.3}}
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search(
            "test", k=5, strategy=RecallStrategy.RELATIONSHIP_REVIEW
        )
        assert len(results) >= 1
        # RELATIONSHIP_REVIEW 策略下 graph_weight 应占主导（0.70）。

    def test_build_score_breakdown_no_doc_result(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """doc_result 为 None 时，_build_score_breakdown 仍应保留 document_* 字段。"""
        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        graph = _make_graph(
            1, 0.7, "graph", {}, kw_score=0.6, vec_score=0.5, breakdown={"gk": 0.4}
        )
        breakdown = retriever._build_score_breakdown(
            doc_result=None,
            graph_result=graph,
            doc_signal=0.0,
            graph_signal=0.7,
            document_weight=0.65,
            graph_weight=0.35,
            route_bonus=0.0,
            final_score=0.7,
            intent="",
        )
        # doc_signal=0 时，document_route_score 应为 0.0。
        assert breakdown.get("document_route_score") == 0.0
        assert "graph_keyword_score" in breakdown
        assert "graph_vector_score" in breakdown

    @pytest.mark.asyncio
    async def test_multi_query_routes_start_concurrently(
        self,
        memory_loader: AsyncMock,
    ) -> None:
        """多条查询必须在任意一条完成前全部启动。"""

        from core.features.retrieval.dual_route_retriever import DualRouteRetriever
        from core.features.retrieval.query_planner import QueryPlan

        queries = ("查询甲", "查询乙", "查询丙")
        started = {query: asyncio.Event() for query in queries}
        release = asyncio.Event()

        async def search_document(query: str, *_args: Any, **_kwargs: Any) -> list[Any]:
            """登记查询启动并等待统一释放。"""

            started[query].set()
            await release.wait()
            return [_make_hybrid(len(query), 0.8, query)]

        document = MagicMock()
        document.search = AsyncMock(side_effect=search_document)
        graph = MagicMock()
        graph.search = AsyncMock(return_value=[])
        retriever = DualRouteRetriever(document, graph, memory_loader)
        plan = QueryPlan(
            original_query="查询甲",
            intent="default",
            entities=(),
            focus_terms=(),
            temporal_anchor=None,
            reference_time=datetime.now(timezone.utc),
            queries=queries,
            required_facets=(),
            ambiguity_flags=(),
            memory_types=(),
        )

        task = asyncio.create_task(retriever.search("查询甲", k=6, query_plan=plan))
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=1.0,
        )
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_multi_query_routes_share_one_absolute_deadline(
        self,
        memory_loader: AsyncMock,
    ) -> None:
        """多查询计划的全部文档与图路必须复用同一绝对截止时间。"""

        from core.features.retrieval.dual_route_retriever import DualRouteRetriever
        from core.features.retrieval.query_planner import QueryPlan

        document = MagicMock()
        document.search = AsyncMock(return_value=[])
        graph = MagicMock()
        graph.search = AsyncMock(return_value=[])
        retriever = DualRouteRetriever(document, graph, memory_loader)
        plan = QueryPlan(
            original_query="查询甲",
            intent="relationship",
            entities=(),
            focus_terms=(),
            temporal_anchor=None,
            reference_time=datetime.now(timezone.utc),
            queries=("查询甲", "查询乙"),
            required_facets=("relation",),
            ambiguity_flags=(),
            memory_types=(),
        )
        deadline = time.perf_counter() + 5.0

        await retriever.search(
            "查询甲",
            k=4,
            query_plan=plan,
            deadline_monotonic=deadline,
        )

        assert document.search.await_count == 2
        assert graph.search.await_count == 2
        assert {
            call.kwargs["deadline_monotonic"]
            for call in document.search.await_args_list
        } == {deadline}
        assert {
            call.kwargs["deadline_monotonic"] for call in graph.search.await_args_list
        } == {deadline}

    @pytest.mark.asyncio
    async def test_privacy_filter_runs_before_final_truncation(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """群聊过滤机密候选后应继续用后续共享候选补足 top-k。"""

        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.99, metadata={"privacy_level": "confidential"}),
            _make_hybrid(2, 0.90, metadata={"privacy_level": "shared"}),
            _make_hybrid(3, 0.80, metadata={"privacy_level": "shared"}),
        ]
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        results = await retriever.search("匿名查询", k=2, chat_type="group")

        assert [item.doc_id for item in results] == [2, 3]

    @pytest.mark.asyncio
    async def test_privacy_filter_backfills_candidates_omitted_by_reranker(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """重排器先截断时，隐私过滤后仍应从基础候选回填。"""

        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        candidates = [
            _make_hybrid(1, 0.99, metadata={"privacy_level": "confidential"}),
            _make_hybrid(2, 0.90, metadata={"privacy_level": "shared"}),
            _make_hybrid(3, 0.80, metadata={"privacy_level": "shared"}),
        ]
        doc_retriever.search.return_value = candidates
        reranker = MagicMock()
        reranker.rerank.return_value = candidates[:2]
        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            reranker=reranker,
        )

        results = await retriever.search("匿名查询", k=2, chat_type="group")

        assert [item.doc_id for item in results] == [2, 3]

    @pytest.mark.asyncio
    async def test_multi_query_privacy_filter_uses_full_shared_budget(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """多查询融合不得在隐私过滤前把候选提前截断为最终 k。"""

        from core.features.retrieval.dual_route_retriever import DualRouteRetriever
        from core.features.retrieval.query_planner import QueryPlan

        confidential = _make_hybrid(
            1,
            0.99,
            metadata={"privacy_level": "confidential"},
        )

        async def search_by_query(query: str, *_args, **_kwargs):
            """为两条查询返回共享机密候选和不同的公开候选。"""

            shared_id = 2 if query == "查询甲" else 3
            return [
                confidential,
                _make_hybrid(
                    shared_id,
                    0.80,
                    metadata={"privacy_level": "shared"},
                ),
            ]

        doc_retriever.search.side_effect = search_by_query
        plan = QueryPlan(
            original_query="匿名查询",
            intent="default",
            entities=(),
            focus_terms=(),
            temporal_anchor=None,
            reference_time=datetime.now(timezone.utc),
            queries=("查询甲", "查询乙"),
            required_facets=(),
            ambiguity_flags=(),
            memory_types=(),
        )
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        results = await retriever.search(
            "匿名查询",
            k=2,
            chat_type="group",
            query_plan=plan,
        )

        assert [item.doc_id for item in results] == [2, 3]

    @pytest.mark.asyncio
    async def test_atom_touch_uses_tracked_background_task(
        self,
        doc_retriever: AsyncMock,
        graph_retriever: AsyncMock,
        memory_loader: AsyncMock,
    ) -> None:
        """Atom 访问反馈不得阻塞检索结果返回。"""

        from core.features.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [_make_hybrid(1, 0.9)]
        memory_loader.return_value = {
            "id": 1,
            "doc_id": 1,
            "text": "content_1",
            "content": "content_1",
            "metadata": {},
        }
        release = asyncio.Event()
        atom = MagicMock()
        atom.search = AsyncMock(
            return_value=[
                SimpleNamespace(parent_memory_id=1, atom_id=7, final_score=0.8)
            ]
        )

        async def blocked_touch(_ids: list[int]) -> None:
            """模拟等待数据库写入的访问反馈。"""

            await release.wait()

        atom.touch_many = AsyncMock(side_effect=blocked_touch)
        tasks: list[asyncio.Task[Any]] = []

        def track(coro: Any) -> None:
            """模拟引擎的受跟踪任务注册。"""

            tasks.append(asyncio.create_task(coro))

        retriever = DualRouteRetriever(
            doc_retriever,
            graph_retriever,
            memory_loader,
            atom_retriever=atom,
            create_tracked_task_cb=track,
        )

        results = await asyncio.wait_for(retriever.search("匿名查询", k=2), timeout=1.0)

        assert [item.doc_id for item in results] == [1]
        assert len(tasks) == 1
        assert not tasks[0].done()
        release.set()
        await tasks[0]
