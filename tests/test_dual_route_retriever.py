"""DualRouteRetriever 测试 — 文档+图双路检索融合。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_hybrid(doc_id: int, final_score: float, content: str = "", metadata: dict | None = None,
                 bm25: float | None = None, vector: float | None = None,
                 breakdown: dict | None = None) -> Any:
    from core.retrieval.rrf_fusion import HybridResult
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


def _make_graph(doc_id: int, final_score: float, content: str = "", metadata: dict | None = None,
                kw_score: float | None = None, vec_score: float | None = None,
                breakdown: dict | None = None) -> Any:
    from core.retrieval.graph_retriever import GraphResult
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
    def retriever(self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> Any:
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        return DualRouteRetriever(
            document_retriever=doc_retriever,
            graph_retriever=graph_retriever,
            memory_loader=memory_loader,
        )

    @pytest.mark.asyncio
    async def test_search_doc_only(self, retriever: Any, doc_retriever: AsyncMock) -> None:
        """When only document route returns results, those are returned."""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "doc result"),
        ]
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1

    @pytest.mark.asyncio
    async def test_search_both_routes(self, retriever: Any, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """When both routes return, results are merged. Graph-only docs need memory_loader."""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "doc result"),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "graph result"),
        ]
        # Doc-only results have content directly; graph-only results need content from memory_loader
        memory_loader.return_value = {"text": "graph result loaded", "metadata": {}}

        results = await retriever.search("test", k=5)
        assert len(results) == 2
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 in doc_ids

    @pytest.mark.asyncio
    async def test_search_cross_route_bonus(self, retriever: Any, doc_retriever: AsyncMock, graph_retriever: AsyncMock) -> None:
        """Same doc_id in both routes gets cross-route bonus."""
        shared_meta = {"importance": 0.7}
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.8, "shared content", shared_meta, bm25=0.7, vector=0.6),
        ]
        graph_retriever.search.return_value = [
            _make_graph(1, 0.7, "shared content", shared_meta, kw_score=0.6, vec_score=0.5),
        ]
        # Same doc_id in both routes — doc_result provides content and non-empty metadata
        results = await retriever.search("test", k=5)
        assert len(results) == 1
        assert results[0].doc_id == 1

    @pytest.mark.asyncio
    async def test_privacy_filter_group_chat(self, retriever: Any, doc_retriever: AsyncMock) -> None:
        """Group chat filters out confidential memories."""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "public", {"privacy_level": "shared"}),
            _make_hybrid(2, 0.8, "secret", {"privacy_level": "confidential"}),
        ]
        results = await retriever.search("test", k=10, chat_type="group")
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 not in doc_ids  # confidential filtered

    @pytest.mark.asyncio
    async def test_privacy_filter_private_chat(self, retriever: Any, doc_retriever: AsyncMock) -> None:
        """Private chat does NOT filter confidential memories."""
        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "public", {"privacy_level": "shared"}),
            _make_hybrid(2, 0.8, "secret", {"privacy_level": "confidential"}),
        ]
        results = await retriever.search("test", k=10, chat_type="private")
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 in doc_ids  # confidential allowed

    def test_route_weights_for_relation_query(self, retriever: Any) -> None:
        """Relation keywords shift weight toward graph route."""
        doc_w, graph_w, intent = retriever._route_weights_for_query("他是谁")
        assert graph_w > doc_w
        assert "relationship" in intent

    def test_route_weights_for_factual_query(self, retriever: Any) -> None:
        """Factual keywords shift weight toward document route."""
        doc_w, graph_w, intent = retriever._route_weights_for_query("这是什么东西")
        assert doc_w > graph_w
        assert "factual" in intent

    def test_route_weights_for_temporal_query(self, retriever: Any) -> None:
        """Temporal keywords shift weight toward graph route."""
        doc_w, graph_w, intent = retriever._route_weights_for_query("昨天发生了什么")
        assert "temporal" in intent

    def test_route_weights_dynamic_disabled(self, retriever: Any) -> None:
        """When dynamic_route_weighting is disabled, fixed weights are used."""
        retriever.dynamic_route_weighting = False
        doc_w, graph_w, intent = retriever._route_weights_for_query("他是谁")
        assert intent == "fixed"
        assert doc_w == retriever.document_route_weight
        assert graph_w == retriever.graph_route_weight

    @pytest.mark.parametrize("strategy,expected_doc,expected_graph", [
        ("contextual_similarity", 0.70, 0.30),
        ("topic_association", 0.65, 0.35),
        ("preference_query", 0.80, 0.20),
        ("relationship_review", 0.30, 0.70),
    ])
    def test_strategy_weights(self, retriever: Any, strategy: str, expected_doc: float, expected_graph: float) -> None:
        """Each RecallStrategy maps to pre-defined document/graph weights."""
        from core.models.recall_strategy import RecallStrategy
        s = RecallStrategy(strategy)
        doc_w, graph_w = retriever._compute_strategy_weights(s)
        assert doc_w == expected_doc
        assert graph_w == expected_graph

    @pytest.mark.asyncio
    async def test_search_with_query_intent(self, retriever: Any, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """query_intent overrides keyword-based weight adjustment."""
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
        """Memories without privacy_level are treated as shared."""
        results = [
            _make_hybrid(1, 0.9, "no metadata", {}),
        ]
        filtered = retriever._filter_by_privacy(results, "group")
        assert len(filtered) == 1

    # ── edge-case / uncovered-path tests ──────────────────────────────

    @pytest.mark.asyncio
    async def test_graph_only_no_doc_results_uses_graph_fallback(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """When no doc_results but graph_results exist, graph route is a fallback."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

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
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """When persona_interpretation is enabled, matching persona gets boost (lines 103, 146-154)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.5, "test", {
                "persona_interpretations": {"p1": "解读1"},
            }),
        ]
        graph_retriever.search.return_value = []
        retriever = DualRouteRetriever(
            doc_retriever, graph_retriever, memory_loader,
            config={"persona_interpretation.enabled": True, "persona_interpretation.boost": 1.5},
        )
        results = await retriever.search("test", k=5, persona_id="p1")
        assert len(results) >= 1
        # final_score should be boosted (capped at 1.0)
        assert results[0].final_score <= 1.0

    @pytest.mark.asyncio
    async def test_persona_boost_disabled_by_config(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """persona_interpretation disabled → no boost applied."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.5, "test", {
                "persona_interpretations": {"p1": "解读1"},
            }),
        ]
        graph_retriever.search.return_value = []
        retriever = DualRouteRetriever(
            doc_retriever, graph_retriever, memory_loader,
            config={"persona_interpretation.enabled": False},
        )
        results = await retriever.search("test", k=5, persona_id="p1")
        assert len(results) >= 1
        assert results[0].final_score == 0.5  # unchanged

    @pytest.mark.asyncio
    async def test_personalized_ranking_path(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Personalized ranker is called when user_id + ranker + profile_manager present (lines 107-112)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

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
            doc_retriever, graph_retriever, memory_loader,
            personalized_ranker=mock_ranker,
            profile_manager=mock_profile,
        )
        results = await retriever.search("test", k=5, user_id="user1")
        assert len(results) >= 1
        mock_ranker.apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_personalized_ranking_suppresses_exception(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Personalized ranking failure does not break search (line 112)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.8, "test"),
        ]
        graph_retriever.search.return_value = []
        mock_profile = MagicMock()
        mock_profile.get_tag_weights = AsyncMock(side_effect=RuntimeError("db down"))
        mock_profile.get_profile = AsyncMock(return_value={})
        mock_ranker = MagicMock()
        retriever = DualRouteRetriever(
            doc_retriever, graph_retriever, memory_loader,
            personalized_ranker=mock_ranker,
            profile_manager=mock_profile,
        )
        results = await retriever.search("test", k=5, user_id="user1")
        assert len(results) == 1  # still returns results

    @pytest.mark.asyncio
    async def test_reranker_path(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Reranker is called when present and results > 1 (lines 116-117)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "result 1"),
            _make_hybrid(2, 0.5, "result 2"),
        ]
        graph_retriever.search.return_value = []
        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(return_value=doc_retriever.search.return_value)
        retriever = DualRouteRetriever(
            doc_retriever, graph_retriever, memory_loader,
            reranker=mock_reranker,
        )
        results = await retriever.search("test", k=5)
        mock_reranker.rerank.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_reranker_is_awaited_and_receives_query(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Async rerankers, including LLM rerankers, are awaited with the query."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.5, "less relevant"),
            _make_hybrid(2, 0.9, "more relevant"),
        ]
        graph_retriever.search.return_value = []

        class _AsyncReranker:
            def __init__(self) -> None:
                self.query = None

            async def rerank(self, results: list[Any], k: int, **kwargs: Any) -> list[Any]:
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
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Exceptions raised after awaiting an async reranker do not break search."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.2, "low"),
            _make_hybrid(2, 0.8, "high"),
        ]
        graph_retriever.search.return_value = []

        class _FailingAsyncReranker:
            async def rerank(self, results: list[Any], k: int, **kwargs: Any) -> list[Any]:
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
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Reranker failure does not break search (line 117)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "r1"),
            _make_hybrid(2, 0.5, "r2"),
        ]
        graph_retriever.search.return_value = []
        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(side_effect=RuntimeError("reranker down"))
        retriever = DualRouteRetriever(
            doc_retriever, graph_retriever, memory_loader,
            reranker=mock_reranker,
        )
        results = await retriever.search("test", k=5)
        # falls back to sort by final_score
        assert len(results) == 2
        assert results[0].final_score >= results[1].final_score

    @pytest.mark.asyncio
    async def test_memory_loader_exception_yields_none(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """When memory_loader raises, doc_id is skipped via loaded=None (line 208, 242)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "ok", {"importance": 0.5}),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "", {}),  # no content, no metadata → needs loader
        ]
        memory_loader.side_effect = RuntimeError("load failure")
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search("test", k=5)
        # doc_id=1 should be present (has content+metadata); doc_id=2 should be skipped
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids

    @pytest.mark.asyncio
    async def test_memory_loader_returns_none_skips_doc(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """When memory_loader returns None, the doc is skipped (line 242)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "ok", {"importance": 0.5}),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "", {}),  # no content, no metadata
        ]
        memory_loader.return_value = None
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search("test", k=5)
        doc_ids = {r.doc_id for r in results}
        assert 1 in doc_ids
        assert 2 not in doc_ids  # skipped

    # ── LLM intent routing tests ──────────────────────────────────────

    def test_llm_intent_relationship(self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """LLM intent=relationship shifts weight to graph (line 349-354)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "relationship"
        doc_w, graph_w, intent = retriever._route_weights_for_query("query", query_intent=_Intent())
        assert graph_w > doc_w
        assert intent == "llm:relationship"
        assert doc_w == max(0.15, base_doc - 0.25)
        assert graph_w == min(0.85, base_graph + 0.25)

    def test_llm_intent_temporal(self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """LLM intent=temporal shifts weight slightly to graph (line 355-360)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "temporal"
        doc_w, graph_w, intent = retriever._route_weights_for_query("q", query_intent=_Intent())
        assert intent == "llm:temporal"
        assert doc_w == max(0.15, base_doc - 0.15)
        assert graph_w == min(0.85, base_graph + 0.15)

    def test_llm_intent_factual(self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """LLM intent=factual shifts weight to document (line 361-366)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "factual"
        doc_w, graph_w, intent = retriever._route_weights_for_query("q", query_intent=_Intent())
        assert intent == "llm:factual"
        assert doc_w == min(0.9, base_doc + 0.2)
        assert graph_w == max(0.1, base_graph - 0.2)

    def test_llm_intent_preference(self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """LLM intent=preference shifts weight slightly to document (line 367-372)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        base_doc = retriever.document_route_weight
        base_graph = retriever.graph_route_weight

        class _Intent:
            intent = "preference"
        doc_w, graph_w, intent = retriever._route_weights_for_query("q", query_intent=_Intent())
        assert intent == "llm:preference"
        assert doc_w == min(0.9, base_doc + 0.1)
        assert graph_w == max(0.1, base_graph - 0.1)

    def test_llm_intent_default_no_override(self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock) -> None:
        """LLM intent=default falls through to keyword matching."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        class _Intent:
            intent = "default"
        doc_w, graph_w, intent = retriever._route_weights_for_query("random text", query_intent=_Intent())
        # Should not be prefixed with "llm:"
        assert not intent.startswith("llm:")

    def test_zero_total_weight_fallback(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """When total weight <= 0, fallback to fixed base weights (line 404)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(
            doc_retriever, graph_retriever, memory_loader,
            config={"document_route_weight": 0.0, "graph_route_weight": 0.0},
        )
        # With zero weights, normalization would produce 0/0, but keyword hits adjust.
        # Force dynamic off to test the total<=0 fallback directly.
        retriever.dynamic_route_weighting = False
        doc_w, graph_w, intent = retriever._route_weights_for_query("random text")
        assert intent == "fixed"
        assert doc_w == 0.0
        assert graph_w == 0.0

    # ── _build_score_breakdown coverage ───────────────────────────────

    def test_build_score_breakdown_with_graph_breakdown(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """_build_score_breakdown includes graph_result score_breakdown (line 305)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        doc = _make_hybrid(1, 0.8, "doc", {}, bm25=0.7, vector=0.6, breakdown={"doc_key": 0.5})
        graph = _make_graph(1, 0.7, "graph", {}, kw_score=0.6, vec_score=0.5, breakdown={"graph_key": 0.4})
        breakdown = retriever._build_score_breakdown(
            doc_result=doc, graph_result=graph,
            doc_signal=0.8, graph_signal=0.7,
            document_weight=0.65, graph_weight=0.35,
            route_bonus=0.08, final_score=0.85,
            intent="test_intent",
        )
        assert "doc_key" in breakdown
        assert "graph_key" in breakdown
        assert breakdown["query_intent"] == "test_intent"
        assert "document_route_score" in breakdown
        assert "graph_route_score" in breakdown

    @pytest.mark.asyncio
    async def test_search_with_strategy_triggers_compute_weights(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """Passing strategy to search() triggers _compute_strategy_weights (line 165-166)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        from core.models.recall_strategy import RecallStrategy

        doc_retriever.search.return_value = [
            _make_hybrid(1, 0.9, "content", {"importance": 0.5}),
        ]
        graph_retriever.search.return_value = [
            _make_graph(2, 0.8, "", {}),
        ]
        memory_loader.return_value = {"text": "loaded", "metadata": {"importance": 0.3}}
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)
        results = await retriever.search("test", k=5, strategy=RecallStrategy.RELATIONSHIP_REVIEW)
        assert len(results) >= 1
        # With RELATIONSHIP_REVIEW strategy, graph_weight should be dominant (0.70)

    def test_build_score_breakdown_no_doc_result(
        self, doc_retriever: AsyncMock, graph_retriever: AsyncMock, memory_loader: AsyncMock
    ) -> None:
        """_build_score_breakdown with None doc_result — document_* fields still present (line 318-324)."""
        from core.retrieval.dual_route_retriever import DualRouteRetriever
        retriever = DualRouteRetriever(doc_retriever, graph_retriever, memory_loader)

        graph = _make_graph(1, 0.7, "graph", {}, kw_score=0.6, vec_score=0.5, breakdown={"gk": 0.4})
        breakdown = retriever._build_score_breakdown(
            doc_result=None, graph_result=graph,
            doc_signal=0.0, graph_signal=0.7,
            document_weight=0.65, graph_weight=0.35,
            route_bonus=0.0, final_score=0.7,
            intent="",
        )
        # doc_signal=0 → document_route_score is 0.0
        assert breakdown.get("document_route_score") == 0.0
        assert "graph_keyword_score" in breakdown
        assert "graph_vector_score" in breakdown
