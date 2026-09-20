"""graph_extractor.py 测试 — GraphExtractor。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.features.memory.graph.domain.models import GraphBoundary
from core.features.recall.processors.atom_graph_extractor import (
    filter_atoms_by_current_facts,
)
from core.features.recall.processors.graph_extractor import (
    CAUSAL_CAUSED_BY,
    CAUSAL_PREVENTS,
    CAUSAL_RESULTS_IN,
    TEMPORAL_AFTER,
    TEMPORAL_BEFORE,
    TEMPORAL_DURING,
    GraphExtractor,
)
from tests.fact_evidence_helpers import fact_evidence, source_evidence

BOUNDARY = GraphBoundary("graph-test", "public", "r1")


def _atom() -> MagicMock:
    return MagicMock(
        parent_memory_id=1,
        parent_scope_key=BOUNDARY.scope_key,
        parent_privacy_level=BOUNDARY.privacy_level,
        parent_revision=BOUNDARY.revision_token,
        source_evidence=source_evidence(),
    )


def _canonical_atom_source(atoms: list, **extra: object) -> tuple[str, dict]:
    """用 Atom 内容构造当前 canonical 的正文与事实元数据。

    Atom 路径只消费事实仍属于当前 canonical 的 Atom，因此测试必须提供与正文
    对齐的 ``key_facts``/``fact_source_evidence``；空内容 Atom 不进入事实集合。
    """

    facts = [
        atom.content
        for atom in atoms
        if isinstance(getattr(atom, "content", None), str) and atom.content
    ]
    metadata = {
        **BOUNDARY.as_params(),
        "key_facts": facts,
        "fact_source_evidence": fact_evidence(facts),
        **extra,
    }
    return "；".join(facts), metadata


class TestGraphExtractorLegacy:
    """Graph extraction from metadata (backward-compatible path)."""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor()

    def test_fact_nodes_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="正文记录了事实A与事实B",
            metadata={**BOUNDARY.as_params(), "key_facts": ["事实A", "事实B"]},
        )
        fact_nodes = [n for n in graph.nodes if n.node_type == "fact"]
        assert len(fact_nodes) >= 2

    def test_topic_nodes_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={**BOUNDARY.as_params(), "topics": ["t1", "t2", "t3"]},
        )
        topic_nodes = [n for n in graph.nodes if n.node_type == "topic"]
        assert len(topic_nodes) >= 3

    def test_participant_nodes_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={**BOUNDARY.as_params(), "participants": ["Alice", "Bob"]},
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
            metadata={**BOUNDARY.as_params(), "participants": ["QQ:10001"]},
        )

        assert any(node.node_key == "person:qq:10001" for node in graph.nodes)

    def test_co_occurs_edges_created(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="fallback",
            metadata={
                **BOUNDARY.as_params(),
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
                **BOUNDARY.as_params(),
                "topics": ["topic1"],
                "key_facts": ["fact1"],
            },
        )
        describe_edges = [e for e in graph.edges if e.relation_type == "describes"]
        assert len(describe_edges) >= 1

    def test_dedup_preserves_order_in_metadata(self, extractor: GraphExtractor) -> None:
        graph = extractor.extract(
            source_memory_id=1,
            content="test",
            metadata={
                **BOUNDARY.as_params(),
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
            metadata={**BOUNDARY.as_params(), "key_facts": ["f1", "f2", "f3", "f4"]},
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
                **BOUNDARY.as_params(),
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
                **BOUNDARY.as_params(),
                "graph": (
                    '{"entities":[{"name":"Alice","type":"person"},'
                    '{"name":"Bob","type":"person"}],'
                    '"relations":[{"source":"Alice","target":"Bob",'
                    '"relation":"knows"}]}'
                ),
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
            content="fallback content fallback fact",
            metadata={
                **BOUNDARY.as_params(),
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
        atom_a = _atom()
        atom_a.content = "Atom A - 用户喜欢咖啡"
        atom_a.confidence = 0.9
        atom_a.session_id = "s1"
        atom_a.persona_id = None
        atom_a.entities = ["咖啡"]
        atom_a.atom_type = "PREFERENCE"
        atom_a.importance = 0.75
        atom_a.ttl_days = 30.0
        atom_a.event_time = time.time() - 3600

        atom_b = _atom()
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
        content, metadata = _canonical_atom_source(sample_atoms)
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=sample_atoms,
        )
        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            atom.content for atom in sample_atoms
        }

    def test_atom_entries_preserve_business_time_metadata(
        self, extractor: GraphExtractor
    ) -> None:
        atom = _atom()
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

        content, metadata = _canonical_atom_source([atom])
        graph = extractor.extract(
            source_memory_id=1, content=content, metadata=metadata, atoms=[atom]
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
        empty = _atom()
        empty.content = ""
        empty.confidence = 0.5
        empty.session_id = None
        empty.persona_id = None
        empty.entities = []
        empty.atom_type = MagicMock()
        empty.atom_type.value = "unknown"
        empty.importance = 0.5
        empty.ttl_days = 1.0

        valid = _atom()
        valid.content = "有效事实"
        valid.confidence = 0.8
        valid.session_id = "s1"
        valid.persona_id = None
        valid.entities = []
        valid.atom_type = MagicMock()
        valid.atom_type.value = "FACTUAL"
        valid.importance = 0.5
        valid.ttl_days = 30.0

        content, metadata = _canonical_atom_source([valid])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[empty, valid],
        )
        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "有效事实"
        }

    def test_atom_with_entities(self, extractor: GraphExtractor) -> None:
        atom = _atom()
        atom.content = "事实内容"
        atom.confidence = 0.8
        atom.session_id = "s1"
        atom.persona_id = None
        atom.entities = ["实体1", "实体2"]
        atom.atom_type = MagicMock()
        atom.atom_type.value = "FACTUAL"
        atom.importance = 0.5
        atom.ttl_days = 30.0

        content, metadata = _canonical_atom_source([atom])
        graph = extractor.extract(
            source_memory_id=1, content=content, metadata=metadata, atoms=[atom]
        )
        topic_nodes = [n for n in graph.nodes if n.node_type == "topic"]
        assert len(topic_nodes) >= 2

    def test_atom_stable_qq_entity_restores_person_role(
        self,
        extractor: GraphExtractor,
    ) -> None:
        """Atom 路径应从父记忆参与者恢复稳定 QQ 的 person 角色。"""

        atom = _atom()
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

        content, metadata = _canonical_atom_source([atom], participants=["QQ:10001"])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[atom],
        )

        assert any(node.node_key == "person:qq:10001" for node in graph.nodes)


class TestTemporalEdges:
    """G1: Temporal edge extraction."""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor()

    def test_temporal_edges_between_atoms(self) -> None:
        now = time.time()
        atom_a = _atom()
        atom_a.content = "Event A"
        atom_a.event_time = now - 7200
        atom_b = _atom()
        atom_b.content = "Event B"
        atom_b.event_time = now - 3600  # 1 hour later

        extractor = GraphExtractor(config={"graph_memory.temporal_edges_enabled": True})
        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
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
        atom_a = _atom()
        atom_a.content = "Event C"
        atom_a.event_time = now
        atom_b = _atom()
        atom_b.content = "Event D"
        atom_b.event_time = now + 1800  # 30 min later = DURING

        extractor = GraphExtractor(config={"graph_memory.temporal_edges_enabled": True})
        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[atom_a, atom_b],
        )
        during_edges = [e for e in graph.edges if e.relation_type == TEMPORAL_DURING]
        assert len(during_edges) >= 1

    def test_single_atom_no_temporal_edges(self) -> None:
        atom = _atom()
        atom.content = "Only event"
        atom.event_time = time.time()

        extractor = GraphExtractor(config={"graph_memory.temporal_edges_enabled": True})
        content, metadata = _canonical_atom_source([atom])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
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
        atom_a = _atom()
        atom_a.content = "Event A"
        atom_a.event_time = now - 3600
        atom_b = _atom()
        atom_b.content = "Event B"
        atom_b.event_time = now

        extractor = GraphExtractor(
            config={"graph_memory.temporal_edges_enabled": False}
        )
        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
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
        atom_a = _atom()
        atom_a.content = "下雨导致了交通堵塞"
        atom_b = _atom()
        atom_b.content = "因此我们迟到了"

        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) >= 1

    def test_causal_caused_by(self, extractor: GraphExtractor) -> None:
        atom_a = _atom()
        atom_a.content = "因为下雨了所以没去"
        atom_b = _atom()
        atom_b.content = "导致我们取消计划"

        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) >= 1

    def test_causal_prevents(self, extractor: GraphExtractor) -> None:
        atom_a = _atom()
        atom_a.content = "提前备份防止数据丢失"
        atom_b = _atom()
        atom_b.content = "避免错误发生"

        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) >= 1

    def test_single_causal_atom_no_edges(self, extractor: GraphExtractor) -> None:
        atom = _atom()
        atom.content = "因为下雨所以没去"

        content, metadata = _canonical_atom_source([atom])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
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
        atom_a = _atom()
        atom_a.content = "导致问题发生"
        atom_b = _atom()
        atom_b.content = "因此需要解决"

        content, metadata = _canonical_atom_source([atom_a, atom_b])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[atom_a, atom_b],
        )
        causal_edges = [
            e
            for e in graph.edges
            if e.relation_type in (CAUSAL_CAUSED_BY, CAUSAL_RESULTS_IN, CAUSAL_PREVENTS)
        ]
        assert len(causal_edges) == 0


class TestGraphExtractorAtomFactBoundary:
    """canonical 事实集合边界：残留 Atom 不得进入图 fact 派生。"""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor(
            config={
                "graph_memory.temporal_edges_enabled": True,
                "graph_memory.causal_edges_enabled": True,
            }
        )

    def test_stale_atom_is_excluded_from_fact_derivation(
        self, extractor: GraphExtractor
    ) -> None:
        """事实已不在当前 canonical 的 Atom 不产生节点、entry 或边。"""

        stale = _atom()
        stale.content = "已删除的旧事实"
        stale.entities = ["旧主题"]
        stale.confidence = 0.8
        stale.atom_type = "FACTUAL"
        current = _atom()
        current.content = "保留的当前事实"
        current.entities = []
        current.confidence = 0.8
        current.atom_type = "FACTUAL"

        content, metadata = _canonical_atom_source([current])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[stale, current],
        )

        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "保留的当前事实"
        }
        assert not any(node.value == "旧主题" for node in graph.nodes)
        assert all("已删除的旧事实" not in entry.content for entry in graph.entries)
        assert all("已删除的旧事实" not in str(edge.metadata) for edge in graph.edges)

    def test_all_stale_atoms_fall_back_to_current_canonical(
        self, extractor: GraphExtractor
    ) -> None:
        """全部 Atom 都失效时回落 canonical 正文派生，不写入残留内容。"""

        stale = _atom()
        stale.content = "已删除的旧事实"
        stale.atom_type = "FACTUAL"
        current = _atom()
        current.content = "保留的当前事实"
        current.atom_type = "FACTUAL"

        content, metadata = _canonical_atom_source([current])
        graph = extractor.extract(
            source_memory_id=1,
            content=content,
            metadata=metadata,
            atoms=[stale],
        )

        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "保留的当前事实"
        }
        assert all("已删除的旧事实" not in entry.content for entry in graph.entries)

    def test_atoms_are_not_consumed_without_aligned_fact_metadata(
        self, extractor: GraphExtractor
    ) -> None:
        """事实表示不可判定时不消费 Atom，按无事实元数据回落正文。"""

        atom = _atom()
        atom.content = "Atom 独有事实"
        atom.atom_type = "FACTUAL"

        graph = extractor.extract(
            source_memory_id=1,
            content="当前 canonical 正文",
            metadata=BOUNDARY.as_params(),
            atoms=[atom],
        )

        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "当前 canonical 正文"
        }
        assert all("Atom 独有事实" not in entry.content for entry in graph.entries)

    def test_unpaired_fact_metadata_is_stripped_when_fact_left_body(
        self, extractor: GraphExtractor
    ) -> None:
        """缺逐事实配对证据的历史行仍按正文成员性准入：旧事实不在正文即剥离。"""

        atom = _atom()
        atom.content = "旧事实"
        atom.atom_type = "FACTUAL"

        graph = extractor.extract(
            source_memory_id=1,
            content="新正文",
            metadata={**BOUNDARY.as_params(), "key_facts": ["旧事实"]},
            atoms=[atom],
        )

        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "新正文"
        }
        assert all("旧事实" not in entry.content for entry in graph.entries)
        assert all("旧事实" not in str(edge.metadata) for edge in graph.edges)

    def test_unpaired_fact_metadata_is_stripped_on_partial_membership(
        self, extractor: GraphExtractor
    ) -> None:
        """历史行的事实只要有一条已不在正文中，整个事实表示都不参与消费。"""

        graph = extractor.extract(
            source_memory_id=1,
            content="新正文仍保留有效事实",
            metadata={**BOUNDARY.as_params(), "key_facts": ["旧事实", "有效事实"]},
        )

        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "新正文仍保留有效事实"
        }
        assert all("旧事实" not in entry.content for entry in graph.entries)

    def test_unpaired_fact_metadata_still_derives_when_facts_in_body(
        self, extractor: GraphExtractor
    ) -> None:
        """历史行的事实仍在当前正文中时保持既有 fact 派生，不误伤合法来源。"""

        atom = _atom()
        atom.content = "旧事实"
        atom.atom_type = "FACTUAL"

        graph = extractor.extract(
            source_memory_id=1,
            content="新正文里的旧事实仍然有效",
            metadata={**BOUNDARY.as_params(), "key_facts": ["旧事实"]},
            atoms=[atom],
        )

        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "旧事实"
        }
        assert any("旧事实" in entry.content for entry in graph.entries)

    def test_filter_keeps_only_entries_in_current_fact_set(self) -> None:
        """过滤函数按规范化条目保留对齐 Atom，缺事实表示时全部丢弃。"""

        stale = _atom()
        stale.content = "旧事实"
        current = _atom()
        current.content = "当前事实"
        content, metadata = _canonical_atom_source([current])

        assert filter_atoms_by_current_facts([stale, current], content, metadata) == [
            current
        ]
        assert filter_atoms_by_current_facts([stale, current], content, {}) == []
        assert filter_atoms_by_current_facts([], content, metadata) == []


class TestGraphExtractorStructuredFactBoundary:
    """结构化图载荷的事实准入：残留 fact 实体不得进入图派生。"""

    @pytest.fixture
    def extractor(self) -> GraphExtractor:
        return GraphExtractor()

    def test_stale_fact_entities_fall_back_to_current_body(
        self, extractor: GraphExtractor
    ) -> None:
        """结构化载荷里的旧事实实体不产生节点/entry/边，回落当前正文派生。"""

        stale = "用户已经搬到上海"
        graph = extractor.extract(
            source_memory_id=1,
            content="用户现在住在杭州",
            metadata={
                **BOUNDARY.as_params(),
                "key_facts": [stale],
                "fact_source_evidence": fact_evidence([stale]),
                "canonical_summary": stale,
                "graph_extraction": {
                    "entities": [{"name": stale, "type": "fact"}],
                    "relations": [
                        {
                            "source": stale,
                            "target": "杭州",
                            "relation": "located_in",
                        }
                    ],
                },
            },
        )

        assert not any(node.value == stale for node in graph.nodes)
        assert all(stale not in entry.content for entry in graph.entries)
        # 全部结构化实体都被剔除时回落当前 canonical 正文派生：回落路径可观察。
        assert {node.value for node in graph.nodes if node.node_type == "fact"} == {
            "用户现在住在杭州"
        }

    def test_aligned_fact_entities_still_derive(
        self, extractor: GraphExtractor
    ) -> None:
        """与正文对齐的 fact 实体照常派生节点与 entry。"""

        fact = "用户现在住在杭州"
        graph = extractor.extract(
            source_memory_id=1,
            content=fact,
            metadata={
                **BOUNDARY.as_params(),
                "key_facts": [fact],
                "fact_source_evidence": fact_evidence([fact]),
                "graph_extraction": {
                    "entities": [{"name": fact, "type": "fact"}],
                    "relations": [],
                },
            },
        )

        assert any(
            node.value == fact and node.node_type == "fact" for node in graph.nodes
        )
        assert any(fact in entry.content for entry in graph.entries)

    def test_relations_to_dropped_facts_are_not_rebuilt(
        self, extractor: GraphExtractor
    ) -> None:
        """被剔除的残留事实不得经关系端点重新变成节点或边。"""

        stale = "用户已经搬到上海"
        graph = extractor.extract(
            source_memory_id=1,
            content="用户现在住在杭州",
            metadata={
                **BOUNDARY.as_params(),
                "graph_extraction": {
                    "entities": [
                        {"name": stale, "type": "fact"},
                        {"name": "杭州", "type": "location"},
                    ],
                    "relations": [
                        {
                            "source": stale,
                            "target": "杭州",
                            "relation": "located_in",
                        },
                        {
                            "source": "杭州",
                            "target": "用户现在住在杭州",
                            "relation": "describes",
                        },
                    ],
                },
            },
        )

        assert not any(node.value == stale for node in graph.nodes)
        assert all(stale not in entry.content for entry in graph.entries)
        assert [edge.relation_type for edge in graph.edges] == ["describes"]

    def test_non_fact_entities_are_not_filtered_by_content(
        self, extractor: GraphExtractor
    ) -> None:
        """person/topic 等非事实实体行为不变，仍按结构化载荷派生。"""

        graph = extractor.extract(
            source_memory_id=1,
            content="正文不含该名字",
            metadata={
                **BOUNDARY.as_params(),
                "graph_extraction": {
                    "entities": [{"name": "Alice", "type": "person"}],
                    "relations": [],
                },
            },
        )

        assert any(node.value == "Alice" for node in graph.nodes)
        assert any("Alice" in entry.content for entry in graph.entries)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("parent_scope_key", None, "graph_boundary_required"),
        ("parent_revision", "r2", "graph_boundary_mismatch"),
        ("parent_memory_id", 2, "graph_boundary_mismatch"),
        ("source_evidence", [], "grounding_user_source_missing"),
        (
            "source_evidence",
            source_evidence(role="assistant"),
            "grounding_user_source_missing",
        ),
    ],
)
def test_atom_graph_rejects_missing_evidence_or_foreign_parent(field, value, reason):
    accepted = _atom()
    accepted.content = "Alice likes coffee"
    rejected = _atom()
    rejected.content = "An unsupported assistant claim"
    setattr(rejected, field, value)
    with pytest.raises(ValueError, match=reason):
        GraphExtractor().extract(1, "", BOUNDARY.as_params(), [accepted, rejected])
