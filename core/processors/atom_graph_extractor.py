"""从记忆原子中提取图节点、关系边与检索条目。"""

from __future__ import annotations

import hashlib
from typing import Any

from ..models.graph_models import ExtractedGraph, GraphEdge, GraphEntry, GraphNode
from .entity_resolver import EntityResolver

# 时序边类型，表示事件发生的先后关系。
TEMPORAL_BEFORE = "before"
TEMPORAL_AFTER = "after"
TEMPORAL_DURING = "during"
_DURING_WINDOW_SEC = 3600.0

# 因果边类型，表示事件间的因果关系。
CAUSAL_CAUSED_BY = "caused_by"
CAUSAL_RESULTS_IN = "results_in"
CAUSAL_PREVENTS = "prevents"

_CAUSAL_PATTERNS: list[tuple[str, str]] = [
    ("导致", CAUSAL_RESULTS_IN),
    ("造成了", CAUSAL_RESULTS_IN),
    ("引起了", CAUSAL_RESULTS_IN),
    ("所以", CAUSAL_RESULTS_IN),
    ("因此", CAUSAL_RESULTS_IN),
    ("于是", CAUSAL_RESULTS_IN),
    ("因为", CAUSAL_CAUSED_BY),
    ("由于", CAUSAL_CAUSED_BY),
    ("起因", CAUSAL_CAUSED_BY),
    ("because", CAUSAL_CAUSED_BY),
    ("due to", CAUSAL_CAUSED_BY),
    ("therefore", CAUSAL_RESULTS_IN),
    ("thus", CAUSAL_RESULTS_IN),
    ("led to", CAUSAL_RESULTS_IN),
    ("caused by", CAUSAL_CAUSED_BY),
    ("resulted in", CAUSAL_RESULTS_IN),
    ("防止", CAUSAL_PREVENTS),
    ("避免", CAUSAL_PREVENTS),
    ("阻止", CAUSAL_PREVENTS),
    ("预防", CAUSAL_PREVENTS),
    ("prevent", CAUSAL_PREVENTS),
    ("avoid", CAUSAL_PREVENTS),
    ("stop", CAUSAL_PREVENTS),
]


def extract_graph_from_atoms(
    source_memory_id: int,
    atoms: list[Any],
    metadata: dict[str, Any] | None,
    *,
    temporal_edges_enabled: bool,
    causal_edges_enabled: bool,
) -> ExtractedGraph:
    """基于独立记忆原子构图，并从父记忆恢复实体角色。

    参数:
        source_memory_id: 原子所属的 canonical memory 标识。
        atoms: 当前记忆持久化的原子列表。
        metadata: 父记忆元数据，用于区分参与者与普通主题。
        temporal_edges_enabled: 是否生成时序边。
        causal_edges_enabled: 是否生成因果边。

    返回:
        保留人物、主题和事实类型的图快照。
    """
    graph = ExtractedGraph()
    node_map: dict[str, GraphNode] = {}
    participant_values = _canonical_metadata_values(metadata, "participants")

    def _add_node(
        node_type: str,
        value: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """添加规范化节点并返回稳定节点键。"""
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

    for atom in atoms:
        atom_confidence = float(getattr(atom, "confidence", 0.7))
        session_id = getattr(atom, "session_id", None)
        persona_id = getattr(atom, "persona_id", None)
        entities = getattr(atom, "entities", []) or []

        # MemoryAtom.entities 合并了 topics 与 participants；父元数据是角色来源。
        entity_nodes: list[tuple[str, str]] = []
        seen_entity_keys: set[str] = set()
        for entity in entities:
            entity_value = str(entity)
            canonical_value = EntityResolver.canonicalize(entity_value)
            node_type = "person" if canonical_value in participant_values else "topic"
            entity_key = _add_node(node_type, entity_value)
            if not entity_key or entity_key in seen_entity_keys:
                continue
            seen_entity_keys.add(entity_key)
            entity_nodes.append((entity_key, node_type))

        atom_type = getattr(atom, "atom_type", "unknown")
        atom_type_str = str(getattr(atom_type, "value", atom_type))
        fact_key = _add_node("fact", atom.content, {"atom_type": atom_type_str})
        if not fact_key:
            continue

        payload = f"fact|{source_memory_id}||{fact_key}|{atom.content}"
        entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        entry_metadata = {
            "source_memory_id": source_memory_id,
            "session_id": session_id,
            "persona_id": persona_id,
            "importance": float(getattr(atom, "importance", 0.5)),
            "graph_confidence": atom_confidence,
            "atom_type": atom_type_str,
            "ttl_days": float(getattr(atom, "ttl_days", 30.0)),
            **_atom_time_metadata(atom),
        }
        graph.entries.append(
            GraphEntry(
                entry_key=entry_key,
                source_memory_id=source_memory_id,
                session_id=session_id,
                persona_id=persona_id,
                entry_type="fact",
                content=f"记忆原子：{atom.content}",
                metadata=entry_metadata,
                node_keys=[fact_key],
                relation_type="fact",
            )
        )

        for entity_key, node_type in entity_nodes:
            edge_confidence = atom_confidence * 0.9
            relation_type = "mentioned_in" if node_type == "person" else "describes"
            entity_label = "参与者" if node_type == "person" else "主题"
            graph.edges.append(
                GraphEdge(
                    source_key=entity_key,
                    target_key=fact_key,
                    relation_type=relation_type,
                    source_memory_id=source_memory_id,
                    confidence=edge_confidence,
                    metadata={"atom_content": atom.content},
                )
            )
            edge_payload = (
                f"edge|{source_memory_id}|{relation_type}|"
                f"{entity_key}|{fact_key}|{atom.content}"
            )
            edge_entry_key = hashlib.sha1(edge_payload.encode("utf-8")).hexdigest()
            graph.entries.append(
                GraphEntry(
                    entry_key=edge_entry_key,
                    source_memory_id=source_memory_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    entry_type="edge",
                    content=(f"{entity_label} {entity_key} 关联到事实：{atom.content}"),
                    metadata={
                        **entry_metadata,
                        "graph_confidence": edge_confidence,
                    },
                    node_keys=[entity_key, fact_key],
                    relation_type=relation_type,
                )
            )

    graph.nodes = list(node_map.values())

    if temporal_edges_enabled:
        graph.edges.extend(_extract_temporal_edges(atoms, source_memory_id, node_map))

    if causal_edges_enabled:
        graph.edges.extend(_extract_causal_edges(atoms, source_memory_id, node_map))

    if not graph.entries:
        for atom in atoms:
            summary_key = _add_node("summary", atom.content)
            if not summary_key:
                continue
            graph.nodes = list(node_map.values())
            payload = f"summary|{source_memory_id}||{summary_key}|{atom.content}"
            summary_entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            graph.entries.append(
                GraphEntry(
                    entry_key=summary_entry_key,
                    source_memory_id=source_memory_id,
                    session_id=getattr(atom, "session_id", None),
                    persona_id=getattr(atom, "persona_id", None),
                    entry_type="summary",
                    content=f"记忆原子：{atom.content}",
                    metadata={
                        "graph_confidence": float(getattr(atom, "confidence", 0.6))
                    },
                    node_keys=[summary_key],
                    relation_type="summary",
                )
            )

    return graph


def _canonical_metadata_values(
    metadata: dict[str, Any] | None,
    key: str,
) -> set[str]:
    """读取父记忆列表字段并返回非空规范值集合。"""
    raw_values = (metadata or {}).get(key, [])
    if not isinstance(raw_values, (list, tuple, set)):
        return set()
    return {
        canonical
        for value in raw_values
        if (canonical := EntityResolver.canonicalize(str(value)))
    }


def _extract_temporal_edges(
    atoms: list[Any],
    source_memory_id: int,
    node_map: dict[str, GraphNode],
) -> list[GraphEdge]:
    """从同一父记忆的原子事件时间中提取时序边。"""
    edges: list[GraphEdge] = []
    timed_atoms: list[tuple[Any, str, float]] = []
    for atom in atoms:
        event_time = getattr(atom, "event_time", None)
        if event_time is None:
            continue
        content = getattr(atom, "content", "")
        if not content:
            continue
        fact_key = ""
        for key, node in node_map.items():
            if node.node_type == "fact" and node.value == content:
                fact_key = key
                break
        if fact_key:
            timed_atoms.append((atom, fact_key, float(event_time)))

    if len(timed_atoms) < 2:
        return edges

    timed_atoms.sort(key=lambda item: item[2])
    for index in range(len(timed_atoms) - 1):
        _, key_a, time_a = timed_atoms[index]
        _, key_b, time_b = timed_atoms[index + 1]
        time_diff = time_b - time_a
        relation_type = (
            TEMPORAL_DURING if time_diff <= _DURING_WINDOW_SEC else TEMPORAL_BEFORE
        )

        edges.append(
            GraphEdge(
                source_key=key_a,
                target_key=key_b,
                relation_type=relation_type,
                source_memory_id=source_memory_id,
                confidence=0.75,
                weight=1.0,
                metadata={
                    "time_diff_sec": round(time_diff, 1),
                    "event_time_a": time_a,
                    "event_time_b": time_b,
                },
            )
        )
        if time_diff > _DURING_WINDOW_SEC:
            edges.append(
                GraphEdge(
                    source_key=key_b,
                    target_key=key_a,
                    relation_type=TEMPORAL_AFTER,
                    source_memory_id=source_memory_id,
                    confidence=0.75,
                    weight=1.0,
                    metadata={
                        "time_diff_sec": round(time_diff, 1),
                        "event_time_a": time_a,
                        "event_time_b": time_b,
                    },
                )
            )

    return edges


def _extract_causal_edges(
    atoms: list[Any],
    source_memory_id: int,
    node_map: dict[str, GraphNode],
) -> list[GraphEdge]:
    """根据原子内容中的因果关键词生成事实间的因果边。"""
    edges: list[GraphEdge] = []
    causal_atoms: list[tuple[Any, str, str]] = []
    for atom in atoms:
        content = getattr(atom, "content", "")
        if not content:
            continue
        content_lower = content.lower()
        for keyword, relation_type in _CAUSAL_PATTERNS:
            if keyword not in content_lower:
                continue
            for key, node in node_map.items():
                if node.node_type == "fact" and node.value == content:
                    causal_atoms.append((atom, key, relation_type))
                    break
            break

    if len(causal_atoms) < 2:
        return edges

    for index, (atom_a, key_a, relation_a) in enumerate(causal_atoms):
        for atom_b, key_b, relation_b in causal_atoms[index + 1 :]:
            if relation_a == CAUSAL_RESULTS_IN:
                source_key, target_key = key_a, key_b
                edge_type = CAUSAL_RESULTS_IN
                time_atom = atom_a
                confidence = 0.65
            elif relation_a == CAUSAL_CAUSED_BY:
                source_key, target_key = key_b, key_a
                edge_type = CAUSAL_RESULTS_IN
                time_atom = atom_b
                confidence = 0.65
            elif relation_a == CAUSAL_PREVENTS:
                source_key, target_key = key_a, key_b
                edge_type = CAUSAL_PREVENTS
                time_atom = atom_a
                confidence = 0.6
            else:
                continue

            edge_metadata = {
                "keyword_a": relation_a,
                "content_a": getattr(atom_a, "content", "")[:80],
                "content_b": getattr(atom_b, "content", "")[:80],
                **_atom_time_metadata(time_atom),
            }
            if edge_type != CAUSAL_PREVENTS:
                edge_metadata["keyword_b"] = relation_b
            edges.append(
                GraphEdge(
                    source_key=source_key,
                    target_key=target_key,
                    relation_type=edge_type,
                    source_memory_id=source_memory_id,
                    confidence=confidence,
                    weight=1.0,
                    metadata=edge_metadata,
                )
            )

    return edges


def _atom_time_metadata(atom: Any) -> dict[str, float]:
    """提取原子的创建时间与业务事件时间。"""
    metadata: dict[str, float] = {}
    create_time = _optional_float(getattr(atom, "created_at", None))
    event_time = _optional_float(getattr(atom, "event_time", None))
    if create_time is not None:
        metadata["create_time"] = create_time
    if event_time is not None:
        metadata["event_time"] = event_time
    return metadata


def _optional_float(value: Any) -> float | None:
    """将可选数值转换为浮点数，布尔值与非法值返回空。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CAUSAL_CAUSED_BY",
    "CAUSAL_PREVENTS",
    "CAUSAL_RESULTS_IN",
    "TEMPORAL_AFTER",
    "TEMPORAL_BEFORE",
    "TEMPORAL_DURING",
    "extract_graph_from_atoms",
]
