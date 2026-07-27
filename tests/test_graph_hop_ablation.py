"""图邻居 hop 和最小距离契约。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.retrieval.graph_keyword_retriever import (
    GraphKeywordResult,
    GraphKeywordRetriever,
)
from core.retrieval.graph_retriever import GraphRetriever
from core.retrieval.rrf_fusion import RRFFusion


def _retriever(*, hops: int) -> tuple[GraphKeywordRetriever, AsyncMock]:
    """构造指定邻居深度的图关键词检索器。"""

    store = AsyncMock()
    store.search_entries_by_bm25.return_value = []
    store.search_nodes_by_tokens.return_value = [{"id": 1, "canonical_value": "项目"}]
    store.get_entries_for_node_ids.return_value = []
    store.get_neighbor_node_ids.return_value = []
    processor = AsyncMock()
    processor.tokenize_async.return_value = ["项目"]
    return (
        GraphKeywordRetriever(
            store,
            processor,
            config={"graph_expansion_hops": hops},
        ),
        store,
    )


@pytest.mark.asyncio
async def test_zero_hop_does_not_query_neighbors() -> None:
    """0 hop 必须完全跳过邻居 Store 查询。"""

    retriever, store = _retriever(hops=0)

    await retriever.search("项目", limit=5)

    store.get_neighbor_node_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_and_two_hops_query_expected_neighbor_depth() -> None:
    """1/2 hop 只应分别执行一层和两层邻居查询。"""

    one_hop, one_store = _retriever(hops=1)
    one_store.get_neighbor_node_ids.return_value = [2]
    await one_hop.search("项目", limit=5)
    assert one_store.get_neighbor_node_ids.await_count == 1

    two_hops, two_store = _retriever(hops=2)
    two_store.get_neighbor_node_ids.side_effect = [[2], [3]]
    await two_hops.search("项目", limit=5)
    assert two_store.get_neighbor_node_ids.await_count == 2


@pytest.mark.asyncio
async def test_multi_path_hit_preserves_minimum_known_distance() -> None:
    """同一 canonical ID 多路径命中时保留最小已知距离。"""

    retriever, store = _retriever(hops=1)
    store.search_entries_by_bm25.return_value = [
        {
            "source_memory_id": 17,
            "score": 0.2,
            "content": "direct",
            "metadata": {},
            "entry_type": "fact",
            "relation_type": "mentions",
        }
    ]
    store.get_neighbor_node_ids.return_value = [2]
    store.get_entries_for_node_ids.side_effect = [
        [],
        [
            {
                "source_memory_id": 17,
                "score": 0.9,
                "content": "neighbor",
                "metadata": {},
                "entry_type": "fact",
                "relation_type": "related",
            }
        ],
    ]

    results = await retriever.search("项目", limit=5)

    assert len(results) == 1
    assert results[0].graph_distance == 0


@pytest.mark.asyncio
async def test_graph_retriever_reports_minimum_distance_in_internal_breakdown() -> None:
    """图距离只进入内部 score_breakdown，且保持安全数值。"""

    keyword = AsyncMock()
    keyword.search.return_value = [
        GraphKeywordResult(
            doc_id=17,
            score=0.9,
            content="derived graph candidate",
            metadata={},
            graph_distance=2,
        )
    ]
    vector = AsyncMock()
    vector.search.return_value = []
    retriever = GraphRetriever(keyword, vector, RRFFusion())

    results = await retriever.search("项目", k=5)

    assert len(results) == 1
    assert results[0].score_breakdown["graph_min_distance"] == 2.0
    assert "graph_min_distance" not in results[0].metadata
