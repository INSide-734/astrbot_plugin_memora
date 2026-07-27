"""图谱全量概览契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.graph_api import GraphApiMixin
from core.models.graph_models import GraphEdge, GraphEntry, GraphNode
from core.storage.graph_store import GraphStore


def _empty_snapshot() -> dict[str, list]:
    """返回不含图元素的标准快照。"""
    return {"nodes": [], "edges": [], "entries": [], "memories": []}


class _GraphApiHost(GraphApiMixin):
    """为全量概览测试提供最小 Page API 宿主。"""

    def __init__(self, graph_store: MagicMock) -> None:
        """保存图存储替身并构造最小记忆引擎。"""
        self._graph_store = graph_store
        self._memory_engine = SimpleNamespace(
            get_statistics=AsyncMock(return_value={"graph_nodes": 0})
        )

    async def _ensure_plugin_ready(self):
        """返回已经就绪的最小插件依赖。"""
        return {"memory_engine": self._memory_engine}, None

    def _get_graph_store(self, memory_engine):
        """返回测试注入的图存储替身。"""
        return self._graph_store

    def _build_graph_view_payload(self, snapshot, stats, **kwargs):
        """构造测试所需的最小图视图响应。"""
        return {**snapshot, "stats": stats, **kwargs}

    def _ok(self, data):
        """构造成功响应。"""
        return {"status": "ok", "data": data}

    def _error(self, message):
        """构造失败响应。"""
        return {"status": "error", "message": message}


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
    node_map = await store.upsert_nodes(nodes)
    edges = [
        GraphEdge(
            source_key=nodes[(memory_id - 1) * 2].node_key,
            target_key=nodes[(memory_id - 1) * 2 + 1].node_key,
            relation_type="related",
            source_memory_id=memory_id,
        )
        for memory_id in range(1, source_count + 1)
    ]
    edge_map = await store.add_edges(edges, node_map)
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
    await store.add_entries(entries, node_map, edge_map)

    snapshot = await store.get_graph_snapshot(full=True)

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
    node_map = await store.upsert_nodes(nodes)
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
    )

    snapshot = await store.get_graph_snapshot(
        session_id="session-a",
        persona_id="persona-a",
        full=True,
    )

    assert [item["memory_id"] for item in snapshot["memories"]] == [1]
    assert [item["label"] for item in snapshot["nodes"]] == ["甲"]


@pytest.mark.asyncio
async def test_overview_without_limits_requests_full_snapshot() -> None:
    """无显式限制的概览端点必须读取全量图快照。"""
    graph_store = MagicMock()
    graph_store.get_graph_snapshot = AsyncMock(return_value=_empty_snapshot())
    host = _GraphApiHost(graph_store)
    request_stub = MagicMock()
    request_stub.args = {}

    with patch("core.api.graph_api.request", request_stub):
        result = await host.get_graph_overview()

    assert result["status"] == "ok"
    graph_store.get_graph_snapshot.assert_awaited_once_with(
        session_id=None,
        persona_id=None,
        full=True,
    )


@pytest.mark.asyncio
async def test_empty_search_requests_full_snapshot() -> None:
    """Dashboard 的无查询搜索必须读取全量图快照。"""
    graph_store = MagicMock()
    graph_store.get_graph_snapshot = AsyncMock(return_value=_empty_snapshot())
    host = _GraphApiHost(graph_store)

    result = await host._query_graph_impl({})

    assert result["status"] == "ok"
    graph_store.get_graph_snapshot.assert_awaited_once_with(
        session_id=None,
        persona_id=None,
        full=True,
    )
