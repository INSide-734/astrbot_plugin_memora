"""GraphKeywordRetriever 测试 — 图记忆的 FTS + 邻居扩展。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestGraphKeywordRetriever:
    @pytest.fixture
    def graph_store(self) -> AsyncMock:
        store = AsyncMock()
        store.search_entries_by_bm25 = AsyncMock(return_value=[])
        store.search_nodes_by_tokens = AsyncMock(return_value=[])
        store.get_entries_for_node_ids = AsyncMock(return_value=[])
        store.get_neighbor_node_ids = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def text_processor(self) -> MagicMock:
        tp = MagicMock()
        tp.tokenize_async = AsyncMock()
        return tp

    @pytest.fixture
    def retriever(self, graph_store: AsyncMock, text_processor: MagicMock) -> Any:
        from core.features.retrieval.graph_keyword_retriever import (
            GraphKeywordRetriever,
        )

        return GraphKeywordRetriever(
            graph_store=graph_store,
            text_processor=text_processor,
        )

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """Empty or whitespace query returns empty list."""
        assert await retriever.search("") == []
        assert await retriever.search("   ") == []

    @pytest.mark.asyncio
    async def test_search_no_tokens(
        self, retriever: Any, text_processor: MagicMock
    ) -> None:
        """When tokenizer returns empty, return empty."""
        text_processor.tokenize_async.return_value = []
        results = await retriever.search("...")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_direct_hits_only(
        self, retriever: Any, graph_store: AsyncMock, text_processor: MagicMock
    ) -> None:
        """Direct BM25 hits are aggregated with weight 1.0."""
        text_processor.tokenize_async.return_value = ["test"]
        graph_store.search_entries_by_bm25.return_value = [
            {
                "source_memory_id": 1,
                "score": 0.9,
                "content": "test memory",
                "metadata": {},
                "entry_type": "fact",
                "relation_type": None,
            },
        ]
        graph_store.search_nodes_by_tokens.return_value = []

        results = await retriever.search("test", limit=5)
        assert len(results) == 1
        assert results[0].doc_id == 1
        assert results[0].score == 0.9
        assert results[0].metadata["graph_match_source"] == "graph_keyword"

    @pytest.mark.asyncio
    async def test_search_with_expansion(
        self, retriever: Any, graph_store: AsyncMock, text_processor: MagicMock
    ) -> None:
        """Neighbor expansion hits are aggregated with lower weight."""
        text_processor.tokenize_async.return_value = ["test"]
        graph_store.search_entries_by_bm25.return_value = []
        graph_store.search_nodes_by_tokens.return_value = [
            {"id": 1, "node_value": "test"}
        ]
        # First call (expansion_hits) returns neighbor entries, second (edge_neighbor) returns empty
        graph_store.get_entries_for_node_ids.side_effect = [
            [
                {
                    "source_memory_id": 2,
                    "score": 0.8,
                    "content": "neighbor memory",
                    "metadata": {},
                    "entry_type": "relation",
                    "relation_type": "friend",
                },
            ],
            [],  # edge_neighbor_hits — empty since no neighbor node IDs
        ]
        graph_store.get_neighbor_node_ids.return_value = []

        results = await retriever.search("test", limit=5)
        assert len(results) == 1
        assert results[0].doc_id == 2
        assert results[0].score == 0.8 * 0.7  # neighbor weight
        assert "graph_neighbor" in results[0].metadata["graph_match_source"]

    @pytest.mark.asyncio
    async def test_search_aggregates_same_doc(
        self, retriever: Any, graph_store: AsyncMock, text_processor: MagicMock
    ) -> None:
        """Same doc hit from multiple sources takes max score."""
        text_processor.tokenize_async.return_value = ["test"]
        graph_store.search_entries_by_bm25.return_value = [
            {
                "source_memory_id": 1,
                "score": 0.7,
                "content": "shared memory",
                "metadata": {},
                "entry_type": "fact",
                "relation_type": None,
            },
        ]
        graph_store.search_nodes_by_tokens.return_value = [{"id": 10}]
        graph_store.get_entries_for_node_ids.return_value = [
            {
                "source_memory_id": 1,
                "score": 0.5,
                "content": "shared memory",
                "metadata": {},
                "entry_type": "relation",
                "relation_type": "friend",
            },
        ]
        graph_store.get_neighbor_node_ids.return_value = []

        results = await retriever.search("test", limit=5)
        assert len(results) == 1
        assert results[0].doc_id == 1
        # The direct BM25 hit (0.7) > neighbor hit (0.5*0.7=0.35), so max should be 0.7
        assert results[0].score > 0.35

    @pytest.mark.asyncio
    async def test_search_edge_expansion(
        self, retriever: Any, graph_store: AsyncMock, text_processor: MagicMock
    ) -> None:
        """Edge-neighbor expansion returns edge hits with lowest weight (lines 125-135)."""
        text_processor.tokenize_async.return_value = ["edge"]
        graph_store.search_entries_by_bm25.return_value = []
        graph_store.search_nodes_by_tokens.return_value = [{"id": 1}]
        graph_store.get_entries_for_node_ids.side_effect = [
            [],  # neighbor hits empty
            [
                {
                    "source_memory_id": 3,
                    "score": 0.6,
                    "content": "edge memory",
                    "metadata": {},
                    "entry_type": "relation",
                    "relation_type": "colleague",
                },
            ],
        ]
        graph_store.get_neighbor_node_ids.return_value = [2]

        results = await retriever.search("edge", limit=5)
        assert len(results) == 1
        assert results[0].doc_id == 3
        assert results[0].score == pytest.approx(0.6 * 0.7)  # edge_neighbor weight
        assert "graph_edge_neighbor" in results[0].metadata["graph_match_source"]

    @pytest.mark.asyncio
    async def test_search_only_keyword_hits(
        self, retriever: Any, graph_store: AsyncMock, text_processor: MagicMock
    ) -> None:
        """When only keyword-only results exist (no BM25, no expansion)."""
        text_processor.tokenize_async.return_value = ["only"]
        graph_store.search_entries_by_bm25.return_value = []
        graph_store.search_nodes_by_tokens.return_value = []
        graph_store.get_entries_for_node_ids.return_value = []

        results = await retriever.search("only", limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_session_id(
        self, retriever: Any, graph_store: AsyncMock, text_processor: MagicMock
    ) -> None:
        """session_id is passed through to graph_store methods."""
        text_processor.tokenize_async.return_value = ["test"]
        graph_store.search_entries_by_bm25.return_value = [
            {
                "source_memory_id": 1,
                "score": 0.9,
                "content": "memory with session",
                "metadata": {},
                "entry_type": "fact",
                "relation_type": None,
            },
        ]
        graph_store.search_nodes_by_tokens.return_value = []

        results = await retriever.search("test", limit=5, session_id="sess_1")
        assert len(results) == 1
        # Verify session_id was passed to BM25 search
        bm25_call = graph_store.search_entries_by_bm25.call_args
        assert bm25_call.kwargs.get("session_id") == "sess_1"
