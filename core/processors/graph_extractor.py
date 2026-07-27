"""从已存储的记忆文档中提取图记忆结构。"""

from __future__ import annotations

import hashlib
from typing import Any

from astrbot.api import logger

from ..models.graph_models import ExtractedGraph, GraphEdge, GraphEntry, GraphNode
from ..security.guardrails import GraphExtractionResult, validate_llm_response
from .atom_graph_extractor import (
    CAUSAL_CAUSED_BY as CAUSAL_CAUSED_BY,
)
from .atom_graph_extractor import (
    CAUSAL_PREVENTS as CAUSAL_PREVENTS,
)
from .atom_graph_extractor import (
    CAUSAL_RESULTS_IN as CAUSAL_RESULTS_IN,
)
from .atom_graph_extractor import (
    TEMPORAL_AFTER as TEMPORAL_AFTER,
)
from .atom_graph_extractor import (
    TEMPORAL_BEFORE as TEMPORAL_BEFORE,
)
from .atom_graph_extractor import (
    TEMPORAL_DURING as TEMPORAL_DURING,
)
from .atom_graph_extractor import (
    extract_graph_from_atoms,
)
from .entity_resolver import EntityResolver


class GraphExtractor:
    """将记忆摘要转换为节点、边与可检索的图条目。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """读取图提取数量限制及时序、因果边开关。"""
        self.config = config or {}
        self.max_topics = int(self.config.get("graph_max_topics", 6))
        self.max_participants = int(self.config.get("graph_max_participants", 8))
        self.max_facts = int(self.config.get("graph_max_facts", 8))
        self.temporal_edges_enabled = bool(
            self.config.get("graph_memory.temporal_edges_enabled", True)
        )
        self.causal_edges_enabled = bool(
            self.config.get("graph_memory.causal_edges_enabled", True)
        )

    def extract(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
        atoms: list | None = None,
    ) -> ExtractedGraph:
        """根据一条记忆文档构建图快照。"""
        if atoms:
            return extract_graph_from_atoms(
                source_memory_id,
                atoms,
                metadata,
                temporal_edges_enabled=self.temporal_edges_enabled,
                causal_edges_enabled=self.causal_edges_enabled,
            )
        guarded = self._validate_structured_graph(metadata)
        if guarded is not None:
            graph = self._extract_from_structured_graph(
                source_memory_id,
                content,
                metadata or {},
                guarded,
            )
            if graph.entries:
                return graph
        return self._extract_legacy(source_memory_id, content, metadata)

    @staticmethod
    def _validate_structured_graph(
        metadata: dict[str, Any] | None,
    ) -> GraphExtractionResult | None:
        """通过护栏校验显式提供的图提取元数据。"""
        metadata = metadata or {}
        payload = None
        for key in ("graph_extraction", "graph_extraction_result", "graph"):
            if key in metadata:
                payload = metadata.get(key)
                break
        if payload is None and {"entities", "relations"}.issubset(metadata):
            payload = {
                "entities": metadata.get("entities"),
                "relations": metadata.get("relations"),
            }
        if payload is None:
            return None

        if isinstance(payload, GraphExtractionResult):
            return payload
        if isinstance(payload, str):
            return validate_llm_response(
                payload,
                GraphExtractionResult,
                fallback_return_none=True,
            )
        if isinstance(payload, dict):
            try:
                return GraphExtractionResult(**payload)
            except Exception:
                logger.warning(
                    "[图提取器] 结构化图元数据未通过护栏校验；已回退到旧版提取流程",
                    exc_info=True,
                )
                return None

        logger.warning(
            "[图提取器] 不支持的结构化图载荷类型：%s",
            type(payload).__name__,
        )
        return None

    def _extract_from_structured_graph(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any],
        guarded: GraphExtractionResult,
    ) -> ExtractedGraph:
        """将通过护栏校验的图数据转换为图记忆模型。"""
        graph = ExtractedGraph()
        node_map: dict[str, GraphNode] = {}
        name_to_key: dict[str, str] = {}
        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        summary = metadata.get("canonical_summary") or content

        def _add_node(
            node_type: str,
            value: str,
            extra: dict[str, Any] | None = None,
        ) -> str:
            """添加结构化实体节点并返回稳定节点键。"""
            canonical_value = EntityResolver.canonicalize(value)
            if not canonical_value:
                return ""
            node = GraphNode(
                node_type=node_type,
                value=value.strip(),
                canonical_value=canonical_value,
                metadata=extra or {},
            )
            node_map[node.node_key] = node
            return node.node_key

        def _confidence(raw: Any, default: float = 0.75) -> float:
            """将结构化置信度限制在零到一之间。"""
            try:
                return max(0.0, min(1.0, float(raw)))
            except (TypeError, ValueError):
                return default

        def _add_entry(
            entry_type: str,
            content_text: str,
            node_keys: list[str],
            relation_type: str | None = None,
            confidence: float = 0.75,
        ) -> None:
            """为结构化图产物添加可检索条目。"""
            payload = (
                f"{entry_type}|{source_memory_id}|{relation_type or ''}|"
                f"{'|'.join(node_keys)}|{content_text}"
            )
            entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            graph.entries.append(
                GraphEntry(
                    entry_key=entry_key,
                    source_memory_id=source_memory_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    entry_type=entry_type,
                    content=content_text,
                    metadata={
                        "source_memory_id": source_memory_id,
                        "session_id": session_id,
                        "persona_id": persona_id,
                        "importance": metadata.get("importance", 0.5),
                        "create_time": metadata.get("create_time"),
                        "last_access_time": metadata.get("last_access_time"),
                        "canonical_summary": summary,
                        "graph_confidence": confidence,
                        "graph_guardrails_validated": True,
                    },
                    node_keys=node_keys,
                    relation_type=relation_type,
                )
            )

        for entity in guarded.entities:
            name = str(entity.get("name", "")).strip()
            node_type = str(entity.get("type", "entity")).strip() or "entity"
            if not name:
                continue
            extra = {
                key: value
                for key, value in entity.items()
                if key not in {"name", "type"}
            }
            extra["graph_guardrails_validated"] = True
            node_key = _add_node(node_type, name, extra)
            if not node_key:
                continue
            name_to_key[name] = node_key
            _add_entry(
                "entity",
                f"实体：{name}（类型：{node_type}）。摘要：{summary}",
                [node_key],
                relation_type="entity",
                confidence=_confidence(entity.get("confidence"), 0.7),
            )

        for relation in guarded.relations:
            source_name = str(relation.get("source", "")).strip()
            target_name = str(relation.get("target", "")).strip()
            relation_type = str(relation.get("relation", "")).strip()
            if not source_name or not target_name or not relation_type:
                continue
            source_key = name_to_key.get(source_name)
            if not source_key:
                source_key = _add_node(
                    "entity",
                    source_name,
                    {
                        "graph_guardrails_validated": True,
                        "generated_from_relation": True,
                    },
                )
                if source_key:
                    name_to_key[source_name] = source_key
            target_key = name_to_key.get(target_name)
            if not target_key:
                target_key = _add_node(
                    "entity",
                    target_name,
                    {
                        "graph_guardrails_validated": True,
                        "generated_from_relation": True,
                    },
                )
                if target_key:
                    name_to_key[target_name] = target_key
            if not source_key or not target_key:
                continue

            confidence = _confidence(relation.get("confidence"), 0.78)
            rel_metadata = {
                key: value
                for key, value in relation.items()
                if key not in {"source", "target", "relation"}
            }
            rel_metadata.update(
                {
                    "summary": summary,
                    "graph_guardrails_validated": True,
                }
            )
            graph.edges.append(
                GraphEdge(
                    source_key=source_key,
                    target_key=target_key,
                    relation_type=relation_type,
                    source_memory_id=source_memory_id,
                    confidence=confidence,
                    metadata=rel_metadata,
                )
            )
            _add_entry(
                "edge",
                (
                    f"实体 {source_name} 与 {target_name} 的关系为 {relation_type}。"
                    f"摘要：{summary}"
                ),
                [source_key, target_key],
                relation_type=relation_type,
                confidence=confidence,
            )

        graph.nodes = list(node_map.values())
        return graph

    def _extract_legacy(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> ExtractedGraph:
        """从 metadata 执行旧版图提取逻辑（向后兼容路径）。"""
        metadata = metadata or {}
        graph = ExtractedGraph()

        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        summary = metadata.get("canonical_summary") or content

        topics = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("topics", []) if item]
        )[: self.max_topics]
        participants = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("participants", []) if item]
        )[: self.max_participants]
        key_facts = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("key_facts", []) if item]
        )[: self.max_facts]

        if not key_facts and summary:
            key_facts = [summary]

        node_map: dict[str, GraphNode] = {}

        def _add_node(
            node_type: str, value: str, extra: dict[str, Any] | None = None
        ) -> str:
            """添加旧版 metadata 节点并返回稳定节点键。"""
            canonical_value = EntityResolver.canonicalize(value)
            if not canonical_value:
                return ""
            node = GraphNode(
                node_type=node_type,
                value=value.strip(),
                canonical_value=canonical_value,
                metadata=extra or {},
            )
            node_map[node.node_key] = node
            return node.node_key

        topic_keys = [_add_node("topic", topic) for topic in topics]
        participant_keys = [
            _add_node("person", participant) for participant in participants
        ]
        fact_keys = [
            _add_node("fact", fact, {"summary": summary}) for fact in key_facts
        ]

        topic_keys = [item for item in topic_keys if item]
        participant_keys = [item for item in participant_keys if item]
        fact_keys = [item for item in fact_keys if item]

        graph.nodes.extend(node_map.values())

        def _add_entry(
            entry_type: str,
            content_text: str,
            node_keys: list[str],
            relation_type: str | None = None,
            confidence: float = 0.8,
        ) -> None:
            """为旧版图产物添加可检索条目。"""
            payload = (
                f"{entry_type}|{source_memory_id}|{relation_type or ''}|"
                f"{'|'.join(node_keys)}|{content_text}"
            )
            entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            entry_metadata = {
                "source_memory_id": source_memory_id,
                "session_id": session_id,
                "persona_id": persona_id,
                "importance": metadata.get("importance", 0.5),
                "create_time": metadata.get("create_time"),
                "last_access_time": metadata.get("last_access_time"),
                "canonical_summary": summary,
                "summary_schema_version": metadata.get("summary_schema_version"),
                "graph_confidence": confidence,
                "source_window": metadata.get("source_window"),
            }
            graph.entries.append(
                GraphEntry(
                    entry_key=entry_key,
                    source_memory_id=source_memory_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    entry_type=entry_type,
                    content=content_text,
                    metadata=entry_metadata,
                    node_keys=node_keys,
                    relation_type=relation_type,
                )
            )

        for fact_key in fact_keys:
            fact_value = node_map[fact_key].value
            _add_entry(
                "fact",
                f"事实：{fact_value}。摘要：{summary}",
                [fact_key],
                relation_type="fact",
                confidence=0.9,
            )

        for topic_key in topic_keys:
            topic_value = node_map[topic_key].value
            _add_entry(
                "topic",
                f"主题：{topic_value}。摘要：{summary}",
                [topic_key],
                relation_type="topic",
                confidence=0.75,
            )

        for person_key in participant_keys:
            person_value = node_map[person_key].value
            _add_entry(
                "participant",
                f"参与者：{person_value}。摘要：{summary}",
                [person_key],
                relation_type="participant",
                confidence=0.7,
            )

        for topic_key in topic_keys:
            for fact_key in fact_keys:
                graph.edges.append(
                    GraphEdge(
                        source_key=topic_key,
                        target_key=fact_key,
                        relation_type="describes",
                        source_memory_id=source_memory_id,
                        confidence=0.82,
                        metadata={"summary": summary},
                    )
                )
                _add_entry(
                    "edge",
                    (
                        f"主题 {node_map[topic_key].value} 描述了"
                        f"事实 {node_map[fact_key].value}。摘要：{summary}"
                    ),
                    [topic_key, fact_key],
                    relation_type="describes",
                    confidence=0.82,
                )

        for person_key in participant_keys:
            for fact_key in fact_keys:
                graph.edges.append(
                    GraphEdge(
                        source_key=person_key,
                        target_key=fact_key,
                        relation_type="mentioned_in",
                        source_memory_id=source_memory_id,
                        confidence=0.88,
                        metadata={"summary": summary},
                    )
                )
                _add_entry(
                    "edge",
                    (
                        f"参与者 {node_map[person_key].value} 与"
                        f"事实 {node_map[fact_key].value} 相关联。摘要：{summary}"
                    ),
                    [person_key, fact_key],
                    relation_type="mentioned_in",
                    confidence=0.88,
                )

        for index, first_key in enumerate(participant_keys):
            for second_key in participant_keys[index + 1 :]:
                graph.edges.append(
                    GraphEdge(
                        source_key=first_key,
                        target_key=second_key,
                        relation_type="co_occurs_with",
                        source_memory_id=source_memory_id,
                        confidence=0.7,
                        metadata={"summary": summary},
                    )
                )
                _add_entry(
                    "edge",
                    (
                        f"参与者 {node_map[first_key].value} 与"
                        f"参与者 {node_map[second_key].value} 共同出现。摘要：{summary}"
                    ),
                    [first_key, second_key],
                    relation_type="co_occurs_with",
                    confidence=0.7,
                )

        if not graph.entries and summary:
            summary_key = _add_node("summary", summary)
            if summary_key:
                graph.nodes = list(node_map.values())
                _add_entry(
                    "summary",
                    f"摘要：{summary}",
                    [summary_key],
                    relation_type="summary",
                    confidence=0.6,
                )

        return graph


__all__ = [
    "CAUSAL_CAUSED_BY",
    "CAUSAL_PREVENTS",
    "CAUSAL_RESULTS_IN",
    "GraphExtractor",
    "TEMPORAL_AFTER",
    "TEMPORAL_BEFORE",
    "TEMPORAL_DURING",
]
