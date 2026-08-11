"""图谱画布轻量快照测试。"""

from __future__ import annotations

import pytest

from core.features.memory.graph.domain.models import GraphEdge, GraphEntry, GraphNode
from core.storage.graph_store import GraphStore


@pytest.mark.asyncio
async def test_canvas_snapshot_returns_all_scope_nodes_and_edges_without_entries(
    tmp_db_path,
) -> None:
    """画布快照保留作用域内全部节点和边，但不构造条目与记忆详情。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    nodes = [
        GraphNode(node_type="fact", value="甲", canonical_value="scope-a"),
        GraphNode(node_type="fact", value="乙", canonical_value="scope-b"),
        GraphNode(node_type="fact", value="丙", canonical_value="scope-c"),
    ]
    node_map = await store.upsert_nodes(nodes)
    edges = [
        GraphEdge(
            source_key=nodes[0].node_key,
            target_key=nodes[1].node_key,
            relation_type="related",
            source_memory_id=1,
        ),
        GraphEdge(
            source_key=nodes[1].node_key,
            target_key=nodes[2].node_key,
            relation_type="caused_by",
            source_memory_id=2,
        ),
    ]
    edge_map = await store.add_edges(edges, node_map)
    await store.add_entries(
        [
            GraphEntry(
                entry_key="scope-a-entry",
                source_memory_id=1,
                session_id="session-a",
                persona_id="persona-a",
                entry_type="edge",
                content="这段长正文不应进入画布响应",
                metadata={"importance": 0.5},
                node_keys=[nodes[0].node_key, nodes[1].node_key],
                relation_type="related",
            ),
            GraphEntry(
                entry_key="scope-b-entry",
                source_memory_id=2,
                session_id="session-b",
                persona_id="persona-a",
                entry_type="edge",
                content="另一个作用域的正文",
                metadata={"importance": 0.8},
                node_keys=[nodes[1].node_key, nodes[2].node_key],
                relation_type="caused_by",
            ),
        ],
        node_map,
        edge_map,
    )

    snapshot = await store.get_canvas_snapshot(
        session_id="session-a",
        persona_id="persona-a",
    )

    assert {item["label"] for item in snapshot["nodes"]} == {"甲", "乙"}
    assert [(item["source"], item["target"]) for item in snapshot["edges"]] == [
        (node_map[nodes[0].node_key], node_map[nodes[1].node_key])
    ]
    assert snapshot["nodes"][0]["entry_count"] == 1
    assert snapshot["nodes"][0]["memory_count"] == 1
    assert "entries" not in snapshot
    assert "memories" not in snapshot


@pytest.mark.asyncio
async def test_canvas_snapshot_filters_edges_and_orphan_nodes_by_time_range(
    tmp_db_path,
) -> None:
    """时间范围只保留命中边及其端点，缺失时间的历史兼容行为另行保留。"""
    store = GraphStore(tmp_db_path)
    await store.initialize()
    now = 1_700_000_000.0
    nodes = [
        GraphNode(node_type="fact", value="近期甲", canonical_value="recent-a"),
        GraphNode(node_type="fact", value="近期乙", canonical_value="recent-b"),
        GraphNode(node_type="fact", value="旧甲", canonical_value="old-a"),
        GraphNode(node_type="fact", value="旧乙", canonical_value="old-b"),
    ]
    node_map = await store.upsert_nodes(nodes)
    edges = [
        GraphEdge(
            source_key=nodes[0].node_key,
            target_key=nodes[1].node_key,
            relation_type="related",
            source_memory_id=1,
            metadata={"event_time": now - 3600},
        ),
        GraphEdge(
            source_key=nodes[2].node_key,
            target_key=nodes[3].node_key,
            relation_type="related",
            source_memory_id=2,
            metadata={"event_time": now - 10 * 24 * 3600},
        ),
    ]
    edge_map = await store.add_edges(edges, node_map)
    await store.add_entries(
        [
            GraphEntry(
                entry_key="recent-entry",
                source_memory_id=1,
                session_id=None,
                persona_id=None,
                entry_type="edge",
                content="近期关系",
                node_keys=[nodes[0].node_key, nodes[1].node_key],
                relation_type="related",
            ),
            GraphEntry(
                entry_key="old-entry",
                source_memory_id=2,
                session_id=None,
                persona_id=None,
                entry_type="edge",
                content="旧关系",
                node_keys=[nodes[2].node_key, nodes[3].node_key],
                relation_type="related",
            ),
        ],
        node_map,
        edge_map,
    )

    snapshot = await store.get_canvas_snapshot(
        oldest_timestamp=now - 7 * 24 * 3600,
    )

    assert {item["label"] for item in snapshot["nodes"]} == {"近期甲", "近期乙"}
    assert len(snapshot["edges"]) == 1
    assert snapshot["edges"][0]["timestamp"] == now - 3600

    historical_snapshot = await store.get_canvas_snapshot(
        oldest_timestamp=now - 20 * 24 * 3600,
        newest_timestamp=now - 2 * 24 * 3600,
    )

    assert {item["label"] for item in historical_snapshot["nodes"]} == {
        "旧甲",
        "旧乙",
    }
    assert len(historical_snapshot["edges"]) == 1
    assert historical_snapshot["edges"][0]["timestamp"] == now - 10 * 24 * 3600
