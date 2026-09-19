"""图谱全量概览契约测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.memory.graph.domain.models import (
    GraphBoundary,
    GraphEdge,
    GraphEntry,
    GraphNode,
)
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.platform.transport.page_api.graph_api import GraphApiMixin

BOUNDARY = GraphBoundary("graph-test", "public", "r1")


class _GraphApiHost(GraphApiMixin):
    """为管理员总览契约提供最小 Page API 宿主。"""

    def __init__(self, graph_store: MagicMock) -> None:
        """保存图存储替身与统计替身，供总览入口读取。"""
        self._graph_store = graph_store
        self._memory_engine = MagicMock()
        self._memory_engine.get_statistics = AsyncMock(return_value={"total": 0})

    def _get_graph_store(self, _memory_engine):
        """返回测试注入的图存储替身。"""
        return self._graph_store

    async def _ensure_plugin_ready(self):
        """返回只用于验证入口门控的最小就绪结果。"""
        return {"memory_engine": self._memory_engine}, None

    def _ok(self, data):
        """返回与生产一致的页面成功 envelope。"""
        from core.platform.transport.page_api.response_utils import ok_response

        return ok_response(data)

    def _error(self, msg):
        """返回与生产一致的页面错误 envelope。"""
        from core.platform.transport.page_api.response_utils import error_response

        return error_response(msg)

    def _build_graph_view_payload(self, snapshot, stats, **kwargs):
        """返回便于断言的管理员视图载荷替身。"""
        result = {
            "nodes": snapshot.get("nodes", []),
            "edges": snapshot.get("edges", []),
            "stats": stats,
        }
        result.update(kwargs)
        return result


@pytest.mark.asyncio
async def test_full_snapshot_returns_every_graph_memory(tmp_db_path) -> None:
    """全量快照不得沿用源记忆、条目、节点或边的旧限制。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    source_count = 125
    nodes = [
        GraphNode(
            node_type="fact",
            value=f"事实 {node_index}",
            canonical_value=f"fact-{node_index}",
        )
        for node_index in range(1, source_count * 2 + 1)
    ]
    node_map = await store.upsert_nodes(nodes, boundary=BOUNDARY)
    edges = [
        GraphEdge(
            source_key=nodes[(memory_id - 1) * 2].node_key,
            target_key=nodes[(memory_id - 1) * 2 + 1].node_key,
            relation_type="related",
            source_memory_id=memory_id,
        )
        for memory_id in range(1, source_count + 1)
    ]
    edge_map = await store.add_edges(edges, node_map, boundary=BOUNDARY)
    entries = [
        GraphEntry(
            entry_key=f"memory-{memory_id}",
            source_memory_id=memory_id,
            session_id=None,
            persona_id=None,
            entry_type="edge",
            content=f"记忆关系 {memory_id}",
            metadata={"importance": 0.5},
            node_keys=[edge.source_key, edge.target_key],
            relation_type=edge.relation_type,
        )
        for memory_id, edge in enumerate(edges, start=1)
    ]
    await store.add_entries(entries, node_map, edge_map, boundary=BOUNDARY)

    snapshot = await store.get_graph_snapshot(full=True, boundary=BOUNDARY)

    assert {item["memory_id"] for item in snapshot["memories"]} == set(
        range(1, source_count + 1)
    )
    assert len(snapshot["entries"]) == source_count
    assert len(snapshot["nodes"]) == source_count * 2
    assert len(snapshot["edges"]) == source_count


@pytest.mark.asyncio
async def test_full_snapshot_preserves_scope_filters(tmp_db_path) -> None:
    """全量模式仍须只返回指定会话与人格范围内的图数据。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    nodes = [
        GraphNode(node_type="fact", value="甲", canonical_value="scope-a"),
        GraphNode(node_type="fact", value="乙", canonical_value="scope-b"),
    ]
    node_map = await store.upsert_nodes(nodes, boundary=BOUNDARY)
    await store.add_entries(
        [
            GraphEntry(
                entry_key="scope-a-entry",
                source_memory_id=1,
                session_id="session-a",
                persona_id="persona-a",
                entry_type="fact",
                content="甲会话记忆",
                node_keys=[nodes[0].node_key],
            ),
            GraphEntry(
                entry_key="scope-b-entry",
                source_memory_id=2,
                session_id="session-b",
                persona_id="persona-b",
                entry_type="fact",
                content="乙会话记忆",
                node_keys=[nodes[1].node_key],
            ),
        ],
        node_map,
        {},
        boundary=BOUNDARY,
    )

    snapshot = await store.get_graph_snapshot(
        session_id="session-a",
        persona_id="persona-a",
        full=True,
        boundary=BOUNDARY,
    )

    assert [item["memory_id"] for item in snapshot["memories"]] == [1]
    assert [item["label"] for item in snapshot["nodes"]] == ["甲"]


@pytest.mark.asyncio
async def test_overview_without_memory_id_returns_admin_canvas() -> None:
    """GET /graph/overview 恢复管理员总览：无 canonical source 也可跨来源浏览。"""
    graph_store = MagicMock()
    graph_store.get_admin_canvas_snapshot = AsyncMock(
        return_value={
            "nodes": [
                {"id": 1, "label": "甲", "type": "fact"},
                {"id": 2, "label": "乙", "type": "topic"},
            ],
            "edges": [{"id": 3, "source": 1, "target": 2, "type": "related"}],
        }
    )
    host = _GraphApiHost(graph_store)
    request_stub = MagicMock()
    request_stub.args = {"session_id": "client-session", "persona_id": "client-persona"}

    with patch("core.platform.transport.page_api.graph_api.request", request_stub):
        result = await host.get_graph_overview()

    assert result["status"] == "ok"
    assert result["data"]["mode"] == "overview"
    assert [node["id"] for node in result["data"]["nodes"]] == [1, 2]
    assert result["data"]["filters"] == {
        "session_id": "client-session",
        "persona_id": "client-persona",
    }


@pytest.mark.asyncio
async def test_empty_search_without_memory_id_returns_admin_canvas() -> None:
    """空搜索回退到管理员总览，而不是要求客户端提供来源边界。"""
    graph_store = MagicMock()
    graph_store.get_admin_canvas_snapshot = AsyncMock(
        return_value={"nodes": [{"id": 1, "label": "甲", "type": "fact"}], "edges": []}
    )
    host = _GraphApiHost(graph_store)

    result = await host._query_graph_impl({})

    assert result["status"] == "ok"
    assert result["data"]["mode"] == "overview"
    assert [node["id"] for node in result["data"]["nodes"]] == [1]
    graph_store.get_admin_canvas_snapshot.assert_awaited_once()
