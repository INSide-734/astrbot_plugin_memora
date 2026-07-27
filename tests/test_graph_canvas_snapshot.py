"""图谱画布轻量快照测试。"""

from __future__ import annotations

import pytest

from core.models.graph_models import GraphEdge, GraphEntry, GraphNode
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
