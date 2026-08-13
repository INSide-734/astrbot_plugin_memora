"""验证原子图提取对人物与主题角色的保留。"""

from unittest.mock import MagicMock

import pytest

from core.features.recall.processors.graph_extractor import GraphExtractor
from core.storage.graph_store import GraphStore


def _make_atom(content: str, entities: list[str]) -> MagicMock:
    """构造带确定性字段的测试记忆原子。"""
    atom = MagicMock()
    atom.content = content
    atom.confidence = 0.8
    atom.session_id = "session-1"
    atom.persona_id = None
    atom.entities = entities
    atom.atom_type = "FACTUAL"
    atom.importance = 0.7
    atom.ttl_days = 30.0
    atom.created_at = None
    atom.event_time = None
    return atom


def test_atom_graph_preserves_participant_and_topic_roles() -> None:
    """原子中的参与者应保持 person 类型，且人物类型优先于同名主题。"""
    extractor = GraphExtractor()
    atom = _make_atom(
        "INSide_734 和大家讨论图谱设计",
        ["图谱设计", "INSide_734", "INSide_734"],
    )

    graph = extractor.extract(
        source_memory_id=1,
        content=atom.content,
        metadata={
            "topics": ["图谱设计", "INSide_734"],
            "participants": ["INSide_734"],
        },
        atoms=[atom],
    )

    nodes_by_key = {node.node_key: node for node in graph.nodes}
    assert "person:inside_734" in nodes_by_key
    assert "topic:inside_734" not in nodes_by_key
    assert "topic:图谱设计" in nodes_by_key

    relations = {(edge.source_key, edge.relation_type) for edge in graph.edges}
    assert ("person:inside_734", "mentioned_in") in relations
    assert ("topic:图谱设计", "describes") in relations
    assert sum(edge.source_key == "person:inside_734" for edge in graph.edges) == 1


def test_same_participant_uses_stable_person_key_across_memories() -> None:
    """不同记忆中的同一参与者应生成相同的 person 节点键。"""
    extractor = GraphExtractor()
    metadata = {
        "topics": ["群聊"],
        "participants": ["INSide_734"],
    }

    first = extractor.extract(
        source_memory_id=1,
        content="第一条事实",
        metadata=metadata,
        atoms=[_make_atom("第一条事实", ["群聊", "INSide_734"])],
    )
    second = extractor.extract(
        source_memory_id=2,
        content="第二条事实",
        metadata=metadata,
        atoms=[_make_atom("第二条事实", ["群聊", "INSide_734"])],
    )

    first_people = {node.node_key for node in first.nodes if node.node_type == "person"}
    second_people = {
        node.node_key for node in second.nodes if node.node_type == "person"
    }
    assert first_people == second_people == {"person:inside_734"}


@pytest.mark.asyncio
async def test_shared_participant_connects_two_memories_in_subgraph(
    tmp_db_path: str,
) -> None:
    """真实图存储应把两条记忆汇聚到同一个人物节点。"""
    extractor = GraphExtractor()
    store = GraphStore(tmp_db_path)
    await store.initialize()
    metadata = {
        "topics": ["群聊"],
        "participants": ["INSide_734"],
    }

    for memory_id, fact in ((1, "第一条事实"), (2, "第二条事实")):
        graph = extractor.extract(
            source_memory_id=memory_id,
            content=fact,
            metadata=metadata,
            atoms=[_make_atom(fact, ["群聊", "INSide_734"])],
        )
        node_ids = await store.upsert_nodes(graph.nodes)
        edge_ids = await store.add_edges(graph.edges, node_ids)
        await store.add_entries(graph.entries, node_ids, edge_ids)

    snapshot = await store.get_subgraph_for_memories([1, 2])
    person_nodes = [
        node for node in snapshot["nodes"] if node["key"] == "person:inside_734"
    ]
    assert len(person_nodes) == 1
    assert person_nodes[0]["memory_count"] == 2
    assert person_nodes[0]["degree"] == 2

    person_id = person_nodes[0]["id"]
    person_edges = [
        edge
        for edge in snapshot["edges"]
        if edge["source"] == person_id and edge["relation_type"] == "mentioned_in"
    ]
    assert len(person_edges) == 2
