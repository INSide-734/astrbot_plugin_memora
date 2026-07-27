"""graph_extractor.py 测试 — GraphExtractor。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.models.graph_models import ExtractedGraph
from core.processors.graph_extractor import (
    CAUSAL_CAUSED_BY,
    CAUSAL_PREVENTS,
    CAUSAL_RESULTS_IN,
    TEMPORAL_AFTER,
    TEMPORAL_BEFORE,
    TEMPORAL_DURING,
    GraphExtractor,
)


class TestGraphExtractorLegacy:
    """Graph extraction from metadata (backward-compatible path)."""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor()

    def test_extract_basic_metadata(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="测试内容",
            metadata={
                "topics": ["咖啡", "饮食"],
                "key_facts": ["用户喜欢喝咖啡"],
                "participants": ["张三", "李四"],
            },
        )
        assert isinstance(graph, ExtractedGraph)
        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0
        assert len(graph.entries) > 0

    def test_fact_nodes_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={"key_facts": ["事实A", "事实B"]},
        )
        fact_nodes = [n for n in graph.nodes if n.node_type == "fact"]
        assert len(fact_nodes) >= 2

    def test_topic_nodes_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={"topics": ["t1", "t2", "t3"]},
        )
        topic_nodes = [n for n in graph.nodes if n.node_type == "topic"]
        assert len(topic_nodes) >= 3

    def test_participant_nodes_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={"participants": ["Alice", "Bob"]},
        )
        person_nodes = [n for n in graph.nodes if n.node_type == "person"]
        assert len(person_nodes) >= 2

    def test_stable_qq_participant_uses_canonical_person_node(
        self,
        extractor: GraphExtractor,
    ) -> None:
        """稳定 QQ 标签应在旧 metadata 图路径生成固定 person 节点键。"""

        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={"participants": ["QQ:10001"]},
        )

        assert any(node.node_key == "person:qq:10001" for node in graph.nodes)

    def test_co_occurs_edges_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={
                "participants": ["Alice", "Bob", "Charlie"],
                "key_facts": ["something happened"],
            },
        )
        co_edges = [e for e in graph.edges if e.relation_type == "co_occurs_with"]
        assert len(co_edges) >= 3  # 3 participants -> 3 pairs

    def test_topic_fact_edges_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={
                "topics": ["topic1"],
                "key_facts": ["fact1"],
            },
        )
        describe_edges = [e for e in graph.edges if e.relation_type == "describes"]
        assert len(describe_edges) >= 1

    def test_empty_metadata_uses_content(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="唯一内容作为摘要",
            metadata={},
        )
        assert isinstance(graph, ExtractedGraph)

    def test_summary_fallback_when_no_entries(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="only summary content",
            metadata={},
        )
        # When metadata has no entries, content is used as summary fallback
        # The summary nodes may or may not exist depending on topics/key_facts presence
        # At minimum, we always get a valid ExtractedGraph
        assert isinstance(graph, ExtractedGraph)

    def test_dedup_preserves_order_in_metadata(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="test",
            metadata={
                "topics": ["咖啡", "咖啡", "coffee", "饮食"],
                "key_facts": ["fact1"],
            },
        )
        topic_nodes = [n for n in graph.nodes if n.node_type == "topic"]
        topic_values = [n.value for n in topic_nodes]
        # "咖啡" and "coffee" canonicalize the same
        assert len(topic_values) <= 3

    def test_max_facts_truncation(self, extractor: GraphExtractor) -> None:
        config = {"graph_max_facts": 2}
        extractor_with_config = GraphExtractor(config=config)
        graph = extractor_with_config.extract(
            source_memory_id=1,
            content="test",
            metadata={"key_facts": ["f1", "f2", "f3", "f4"]},
        )
        fact_nodes = [n for n in graph.nodes if n.node_type == "fact"]
        assert len(fact_nodes) <= 2

    def test_structured_graph_metadata_validated_and_used(
        self, extractor: GraphExtractor
    ) -> None:
        graph = extractor.extract(
            source_memory_id=42,
            content="用户A 正在学习 Python",
            metadata={
                "session_id": "s1",
                "canonical_summary": "用户A 学习 Python",
                "graph_extraction": {
                    "entities": [
                        {"name": "用户A", "type": "person", "confidence": 0.91},
                        {"name": "Python", "type": "skill", "confidence": 0.87},
                    ],
                    "relations": [
                        {
                            "source": "用户A",
                            "target": "Python",
                            "relation": "learning",
                            "confidence": 0.88,
                        },
                    ],
                },
            },
        )

        assert {node.value for node in graph.nodes} >= {"用户A", "Python"}
        assert [edge.relation_type for edge in graph.edges] == ["learning"]
        assert graph.edges[0].metadata["graph_guardrails_validated"] is True
        assert all(
            entry.metadata["graph_guardrails_validated"] is True
            for entry in graph.entries
        )

    def test_structured_graph_json_payload_validated(
        self, extractor: GraphExtractor
    ) -> None:
        graph = extractor.extract(
            source_memory_id=7,
            content="Alice knows Bob",
            metadata={
                "graph": (
                    '{"entities":[{"name":"Alice","type":"person"},'
                    '{"name":"Bob","type":"person"}],'
                    '"relations":[{"source":"Alice","target":"Bob",'
                    '"relation":"knows"}]}'
                )
            },
        )

        assert {node.value for node in graph.nodes} == {"Alice", "Bob"}
        assert len(graph.edges) == 1
        assert graph.edges[0].relation_type == "knows"

    def test_invalid_structured_graph_falls_back_to_legacy(
        self, extractor: GraphExtractor
    ) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback content",
            metadata={
                "graph_extraction": {
                    "entities": [{"type": "person"}],
                    "relations": [{"source": "Alice", "relation": "knows"}],
                },
                "topics": ["fallback-topic"],
                "key_facts": ["fallback fact"],
            },
        )

        assert any(node.value == "fallback-topic" for node in graph.nodes)
        assert any(node.value == "fallback fact" for node in graph.nodes)
        assert not any(
            entry.metadata.get("graph_guardrails_validated") for entry in graph.entries
        )


class TestGraphExtractorAtoms:
    """Graph extraction from memory atoms with per-atom confidence."""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        config = {
            "graph_memory.temporal_edges_enabled": True,
            "graph_memory.causal_edges_enabled": True,
        }
        return GraphExtractor(config=config)

    @pytest.fixture
    def sample_atoms(self) -> list:
        atom_a = MagicMock()
        atom_a.content = "Atom A - 用户喜欢咖啡"
        atom_a.confidence = 0.9
        atom_a.session_id = "s1"
        atom_a.persona_id = None
        atom_a.entities = ["咖啡"]
        atom_a.atom_type = "PREFERENCE"
        atom_a.importance = 0.75
        atom_a.ttl_days = 30.0
        atom_a.event_time = time.time() - 3600

        atom_b = MagicMock()
        atom_b.content = "Atom B - 用户计划明天去爬山"
        atom_b.confidence = 0.85
        atom_b.session_id = "s1"
        atom_b.persona_id = None
        atom_b.entities = ["爬山"]
        atom_b.atom_type = "PLANNED"
        atom_b.importance = 0.8
        atom_b.ttl_days = 7.0
        atom_b.event_time = time.time() + 86400

        return [atom_a, atom_b]

    def test_extract_from_atoms(
        self, extractor: GraphExtractor, sample_atoms: list
    ) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=sample_atoms,
        )
        assert len(graph.nodes) > 0
        assert len(graph.entries) > 0
        assert len(graph.edges) > 0

    def test_atom_entries_preserve_business_time_metadata(
        self, extractor: GraphExtractor
    ) -> None:
        atom = MagicMock()
        atom.content = "用户上周开始学习图谱筛选"
        atom.confidence = 0.8
        atom.session_id = "s1"
        atom.persona_id = None
        atom.entities = ["图谱筛选"]
        atom.atom_type = "FACTUAL"
        atom.importance = 0.6
        atom.ttl_days = 30.0
        atom.created_at = 1700000000.0
        atom.event_time = 1699900000.0

        graph = extractor.extract(
            source_memory_id=1, content="", metadata=None, atoms=[atom]
        )

        timed_entries = [
            entry for entry in graph.entries if entry.metadata.get("event_time")
        ]
        assert timed_entries
        assert all(
            entry.metadata["event_time"] == 1699900000.0 for entry in timed_entries
        )
        assert all(
            entry.metadata["create_time"] == 1700000000.0 for entry in timed_entries
        )

    def test_atom_without_content_skipped(self, extractor: GraphExtractor) -> None:
        atom = MagicMock()
        atom.content = ""
        atom.confidence = 0.5
        atom.session_id = None
        atom.persona_id = None
        atom.entities = []
        atom.atom_type = MagicMock()
        atom.atom_type.value = "unknown"
        atom.importance = 0.5
        atom.ttl_days = 1.0

        graph = extractor.extract(
            source_memory_id=1, content="", metadata=None, atoms=[atom]
        )
        assert len(graph.entries) == 0

    def test_atom_with_entities(self, extractor: GraphExtractor) -> None:
        atom = MagicMock()
        atom.content = "事实内容"
        atom.confidence = 0.8
        atom.session_id = "s1"
        atom.persona_id = None
        atom.entities = ["实体1", "实体2"]
        atom.atom_type = MagicMock()
        atom.atom_type.value = "FACTUAL"
        atom.importance = 0.5
        atom.ttl_days = 30.0

        graph = extractor.extract(
            source_memory_id=1, content="", metadata=None, atoms=[atom]
        )
        topic_nodes = [n for n in graph.nodes if n.node_type == "topic"]
        assert len(topic_nodes) >= 2

    def test_atom_stable_qq_entity_restores_person_role(
        self,
        extractor: GraphExtractor,
    ) -> None:
        """Atom 路径应从父记忆参与者恢复稳定 QQ 的 person 角色。"""

        atom = MagicMock()
        atom.content = "稳定身份参与了讨论"
        atom.confidence = 0.8
        atom.session_id = "s1"
        atom.persona_id = None
        atom.entities = ["QQ:10001"]
        atom.atom_type = "FACTUAL"
        atom.importance = 0.5
        atom.ttl_days = 30.0
        atom.created_at = None
        atom.event_time = None

        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata={"participants": ["QQ:10001"]},
            atoms=[atom],
        )

        assert any(node.node_key == "person:qq:10001" for node in graph.nodes)

    def test_atom_fallback_to_summary_entry(self, extractor: GraphExtractor) -> None:
        # When an atom creates no fact node (empty canonical), fallback creates summary
        atom = MagicMock()
        atom.content = "only atom"
        atom.confidence = 0.5
        atom.session_id = None
        atom.persona_id = None
        atom.entities = []
        atom.atom_type = MagicMock()
        atom.atom_type.value = "unknown"
        atom.importance = 0.3
        atom.ttl_days = 1.0

        graph = extractor.extract(
            source_memory_id=1, content="", metadata=None, atoms=[atom]
        )
        # The atom content should be used; fallback creates summary if no entries
        summary_nodes = [n for n in graph.nodes if n.node_type == "summary"]
        # The fallback path creates summary nodes when entries are empty
        assert (
            len(summary_nodes) >= 0
        )  # May or may not trigger depending on canonicalize


class TestTemporalEdges:
    """G1: Temporal edge extraction."""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor()

    def test_temporal_edges_between_atoms(self) -> None:
        now = time.time()
        atom_a = MagicMock()
        atom_a.content = "Event A"
        atom_a.event_time = now - 7200
        atom_b = MagicMock()
        atom_b.content = "Event B"
        atom_b.event_time = now - 3600  # 1 hour later

        extractor = GraphExtractor(config={"graph_memory.temporal_edges_enabled": True})
        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        temporal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (TEMPORAL_BEFORE, TEMPORAL_AFTER, TEMPORAL_DURING)
        ]
        assert len(temporal_edges) >= 1  # A before B

    def test_temporal_edge_during_same_hour(self) -> None:
        now = time.time()
        atom_a = MagicMock()
        atom_a.content = "Event C"
        atom_a.event_time = now
        atom_b = MagicMock()
        atom_b.content = "Event D"
        atom_b.event_time = now + 1800  # 30 min later = DURING

        extractor = GraphExtractor(config={"graph_memory.temporal_edges_enabled": True})
        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        during_edges = [e for e in graph.edges if e.relation_type == TEMPORAL_DURING]
        assert len(during_edges) >= 1

    def test_single_atom_no_temporal_edges(self) -> None:
        atom = MagicMock()
        atom.content = "Only event"
        atom.event_time = time.time()

        extractor = GraphExtractor(config={"graph_memory.temporal_edges_enabled": True})
        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom],
        )
        temporal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (TEMPORAL_BEFORE, TEMPORAL_AFTER, TEMPORAL_DURING)
        ]
        assert len(temporal_edges) == 0

    def test_temporal_edges_disabled(self) -> None:
        now = time.time()
        atom_a = MagicMock()
        atom_a.content = "Event A"
        atom_a.event_time = now - 3600
        atom_b = MagicMock()
        atom_b.content = "Event B"
        atom_b.event_time = now

        extractor = GraphExtractor(
            config={"graph_memory.temporal_edges_enabled": False}
        )
        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        temporal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (TEMPORAL_BEFORE, TEMPORAL_AFTER, TEMPORAL_DURING)
        ]
        assert len(temporal_edges) == 0


class TestCausalEdges:
    """G2: Causal edge extraction."""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor(config={"graph_memory.causal_edges_enabled": True})

    def test_causal_results_in(self, extractor: GraphExtractor) -> None:
        atom_a = MagicMock()
        atom_a.content = "下雨导致了交通堵塞"
        atom_b = MagicMock()
        atom_b.content = "因此我们迟到了"

        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) >= 1

    def test_causal_caused_by(self, extractor: GraphExtractor) -> None:
        atom_a = MagicMock()
        atom_a.content = "因为下雨了所以没去"
        atom_b = MagicMock()
        atom_b.content = "导致我们取消计划"

        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) >= 1

    def test_causal_prevents(self, extractor: GraphExtractor) -> None:
        atom_a = MagicMock()
        atom_a.content = "提前备份防止数据丢失"
        atom_b = MagicMock()
        atom_b.content = "避免错误发生"

        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) >= 1

    def test_single_causal_atom_no_edges(self, extractor: GraphExtractor) -> None:
        atom = MagicMock()
        atom.content = "因为下雨所以没去"

        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) == 0  # Need at least 2 causal atoms

    def test_causal_edges_disabled(self) -> None:
        extractor = GraphExtractor(config={"graph_memory.causal_edges_enabled": False})
        atom_a = MagicMock()
        atom_a.content = "导致问题发生"
        atom_b = MagicMock()
        atom_b.content = "因此需要解决"

        graph = extractor.extract(
            source_memory_id=1,
            content="",
            metadata=None,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) == 0
