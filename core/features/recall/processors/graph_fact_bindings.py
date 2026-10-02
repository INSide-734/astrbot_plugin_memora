"""fact 级图关系绑定：所有图提取路径共享的唯一绑定规范化与关系计划。

永久图关系只允许来自两类明确证据：

1. ``metadata["fact_bindings"]`` 中逐条声明的 fact 绑定
   （``{"fact_index", "target", "target_type"}``）；
2. 结构化图载荷里带 fact 归属的关系（``fact_index`` 或端点本身就是已准入事实）。

独立的 ``topics``/``participants``/``entities`` 集合只提供节点与 entry 信号，
不再与全部 facts 做笛卡尔积。绑定的 fact 必须仍与当前正文对齐
（``facts_aligned`` 为 ``ALIGNED``）并带用户来源证据；缺失、越界、重复或无法
证明的绑定一律丢弃（fail-closed），不猜测、不合成端点。绑定数据只是 canonical
事实的派生注解，不成为新的事实身份或正文 owner。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ....platform.security.guardrails import GraphExtractionResult
from ...memory.application.fact_text_alignment import (
    FactTextAlignment,
    fact_in_content,
    facts_aligned,
    normalize_fact,
)
from ...memory.domain.memory_atom import has_user_source_evidence
from ...memory.graph.domain.models import (
    ExtractedGraph,
    GraphEdge,
    GraphEntry,
    GraphNode,
)
from .entity_resolver import EntityResolver

FACT_BINDINGS_KEY = "fact_bindings"
MAX_FACT_BINDINGS = 64
EVIDENCE_FACT_BINDING = "explicit_fact_binding"
EVIDENCE_STRUCTURED_RELATION = "structured_relation"
EVIDENCE_ATOM_TEMPORAL = "atom_temporal"
EVIDENCE_ATOM_CAUSAL = "atom_causal"

# 绑定目标类型 → 关系类型（方向固定为 target → fact）。
_TARGET_RELATIONS = {
    "topic": "describes",
    "participant": "mentioned_in",
    "entity": "mentioned_in",
}
_MAX_RELATION_TYPE_LENGTH = 64


@dataclass(frozen=True, slots=True)
class FactBinding:
    """一条经校验、可生成永久关系的 fact 级证据。"""

    fact_index: int
    source_node: GraphNode
    target_node: GraphNode
    relation_type: str
    confidence: float
    evidence_kind: str
    binding_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _clamp_confidence(raw: Any, default: float) -> float:
    """把置信度限制在 [0, 1]；非法值使用默认值。"""

    if isinstance(raw, bool):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return max(0.0, min(1.0, value))


def _valid_index(raw: Any) -> int | None:
    """只接受非布尔、非负整数下标。"""

    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


def is_stale_fact_name(name: Any, stale_fact_keys: frozenset[str]) -> bool:
    """判断名字是否是被改写掉的旧事实文本（与来源字段和自声明类型无关）。"""

    if not stale_fact_keys or not isinstance(name, str):
        return False
    return normalize_fact(name) in stale_fact_keys


def admitted_facts(
    content: str, metadata: dict[str, Any] | None, *, max_facts: int
) -> dict[int, str]:
    """返回可作为关系证据的事实：原始下标 → 事实原文。

    只有事实表示与当前正文 ``ALIGNED`` 时才返回；每条事实还必须带用户来源
    证据，且下标在 ``max_facts`` 以内（与图 fact 节点的数量上限一致）。
    """

    if not isinstance(metadata, dict):
        return {}
    facts = metadata.get("key_facts")
    evidence = metadata.get("fact_source_evidence")
    if (
        not isinstance(facts, list)
        or facts_aligned(content, facts, evidence) is not FactTextAlignment.ALIGNED
        or not isinstance(evidence, list)
    ):
        return {}
    admitted: dict[int, str] = {}
    for index, fact in enumerate(facts[: max(0, max_facts)]):
        if not isinstance(fact, str) or not fact.strip():
            continue
        if index >= len(evidence) or not has_user_source_evidence(evidence[index]):
            continue
        admitted[index] = fact.strip()
    return admitted


def admitted_labels(
    metadata: dict[str, Any] | None,
    field_name: str,
    *,
    limit: int,
    stale_fact_keys: frozenset[str],
) -> list[str]:
    """读取 topics/participants 列表：去重、剔除旧事实文本并按上限截断。"""

    raw = (metadata or {}).get(field_name, [])
    if not isinstance(raw, (list, tuple)):
        return []
    values = EntityResolver.dedupe_preserve_order([str(item) for item in raw if item])
    return [
        value for value in values if not is_stale_fact_name(value, stale_fact_keys)
    ][: max(0, limit)]


def declared_structured_entities(
    content: str,
    guarded: GraphExtractionResult | None,
    stale_fact_keys: frozenset[str],
) -> tuple[dict[str, GraphNode], set[str]]:
    """返回结构化载荷中可用的实体节点（规范名 → 节点）与被剔除的名字。

    ``fact`` 类型实体必须仍出现在当前正文中；记录过但已不在正文中的事实文本
    无论自声明类型如何都剔除。关系端点只能引用这里返回的实体，不再按端点
    名字合成新节点。
    """

    declared: dict[str, GraphNode] = {}
    dropped: set[str] = set()
    if guarded is None:
        return declared, dropped
    for entity in guarded.entities:
        name = str(entity.get("name", "")).strip()
        node_type = str(entity.get("type", "entity")).strip() or "entity"
        if not name:
            continue
        if (node_type == "fact" and not fact_in_content(content, name)) or (
            is_stale_fact_name(name, stale_fact_keys)
        ):
            dropped.add(name)
            continue
        canonical = EntityResolver.canonicalize(name)
        if not canonical or canonical in declared:
            continue
        extra = {
            key: value for key, value in entity.items() if key not in {"name", "type"}
        }
        extra["graph_guardrails_validated"] = True
        declared[canonical] = GraphNode(
            node_type=node_type,
            value=name,
            canonical_value=canonical,
            metadata=extra,
        )
    return declared, dropped


def _fact_node(fact: str, summary: str) -> GraphNode | None:
    canonical = EntityResolver.canonicalize(fact)
    if not canonical:
        return None
    return GraphNode(
        node_type="fact",
        value=fact,
        canonical_value=canonical,
        metadata={"summary": summary},
    )


def _label_node(node_type: str, value: str) -> GraphNode | None:
    canonical = EntityResolver.canonicalize(value)
    if not canonical:
        return None
    return GraphNode(node_type=node_type, value=value, canonical_value=canonical)


def _explicit_bindings(
    metadata: dict[str, Any],
    facts: dict[int, str],
    *,
    summary: str,
    topics: dict[str, str],
    participants: dict[str, str],
    entities: dict[str, GraphNode],
) -> list[FactBinding]:
    """校验 ``fact_bindings``：非法、越界、重复或未声明目标一律丢弃。"""

    raw_bindings = metadata.get(FACT_BINDINGS_KEY)
    if not isinstance(raw_bindings, list) or not facts:
        return []
    bindings: list[FactBinding] = []
    seen: set[str] = set()
    for raw in raw_bindings[:MAX_FACT_BINDINGS]:
        if not isinstance(raw, dict):
            continue
        index = _valid_index(raw.get("fact_index"))
        if index is None or index not in facts:
            continue
        fact = facts[index]
        declared_fact = raw.get("fact")
        if declared_fact is not None and (
            not isinstance(declared_fact, str)
            or normalize_fact(declared_fact) != normalize_fact(fact)
        ):
            # 绑定自带的事实文本与当前下标不一致：下标已陈旧，不可证明。
            continue
        target_type = raw.get("target_type")
        target = raw.get("target")
        if target_type not in _TARGET_RELATIONS or not isinstance(target, str):
            continue
        canonical = EntityResolver.canonicalize(target)
        if not canonical:
            continue
        if target_type == "topic":
            value = topics.get(canonical)
            target_node = _label_node("topic", value) if value else None
        elif target_type == "participant":
            value = participants.get(canonical)
            target_node = _label_node("person", value) if value else None
        else:
            target_node = entities.get(canonical)
        if target_node is None:
            continue
        fact_node = _fact_node(fact, summary)
        if fact_node is None:
            continue
        relation_type = _TARGET_RELATIONS[target_type]
        binding_key = f"fact:{index}:{target_type}:{target_node.node_key}"
        if binding_key in seen:
            continue
        seen.add(binding_key)
        bindings.append(
            FactBinding(
                fact_index=index,
                source_node=target_node,
                target_node=fact_node,
                relation_type=relation_type,
                confidence=_clamp_confidence(raw.get("confidence"), 0.82),
                evidence_kind=EVIDENCE_FACT_BINDING,
                binding_key=binding_key,
            )
        )
    return bindings


def _structured_bindings(
    guarded: GraphExtractionResult | None,
    facts: dict[int, str],
    *,
    summary: str,
    entities: dict[str, GraphNode],
    dropped_names: set[str],
    stale_fact_keys: frozenset[str],
) -> list[FactBinding]:
    """结构化关系必须带 fact 归属，端点必须是已声明实体或已准入事实。"""

    if guarded is None or not facts:
        return []
    fact_lookup = {normalize_fact(fact): index for index, fact in facts.items()}
    bindings: list[FactBinding] = []
    seen: set[str] = set()

    def _resolve(name: str) -> tuple[GraphNode | None, int | None]:
        if name in dropped_names or is_stale_fact_name(name, stale_fact_keys):
            return None, None
        fact_index = fact_lookup.get(normalize_fact(name))
        if fact_index is not None:
            return _fact_node(facts[fact_index], summary), fact_index
        return entities.get(EntityResolver.canonicalize(name)), None

    for relation in guarded.relations:
        source_name = str(relation.get("source", "")).strip()
        target_name = str(relation.get("target", "")).strip()
        relation_type = str(relation.get("relation", "")).strip()
        if (
            not source_name
            or not target_name
            or not relation_type
            or len(relation_type) > _MAX_RELATION_TYPE_LENGTH
        ):
            continue
        source_node, source_fact = _resolve(source_name)
        target_node, target_fact = _resolve(target_name)
        if source_node is None or target_node is None:
            continue
        if source_node.node_key == target_node.node_key:
            continue
        declared_index = relation.get("fact_index")
        if declared_index is not None:
            index = _valid_index(declared_index)
            if index is None or index not in facts:
                continue
        elif source_fact is not None:
            index = source_fact
        elif target_fact is not None:
            index = target_fact
        else:
            # 没有 fact 归属的关系只是共现猜测，不形成永久边。
            continue
        binding_key = (
            f"fact:{index}:relation:{source_node.node_key}|"
            f"{relation_type}|{target_node.node_key}"
        )
        if binding_key in seen:
            continue
        seen.add(binding_key)
        extra = {
            key: value
            for key, value in relation.items()
            if key not in {"source", "target", "relation", "fact_index"}
        }
        extra["graph_guardrails_validated"] = True
        bindings.append(
            FactBinding(
                fact_index=index,
                source_node=source_node,
                target_node=target_node,
                relation_type=relation_type,
                confidence=_clamp_confidence(relation.get("confidence"), 0.78),
                evidence_kind=EVIDENCE_STRUCTURED_RELATION,
                binding_key=binding_key,
                metadata=extra,
            )
        )
    return bindings


def plan_fact_bindings(
    content: str,
    metadata: dict[str, Any] | None,
    *,
    guarded: GraphExtractionResult | None,
    stale_fact_keys: frozenset[str],
    max_facts: int,
    max_topics: int,
    max_participants: int,
) -> list[FactBinding]:
    """生成与提取路径无关的关系计划：显式 fact 绑定 + 带 fact 归属的结构化关系。

    同一 canonical 快照无论是否加载 Atom、是否走结构化或 legacy 表示，都得到
    同一组关系；``metadata`` 应是已按当前正文回落过的事实元数据。
    """

    if not isinstance(metadata, dict):
        return []
    facts = admitted_facts(content, metadata, max_facts=max_facts)
    if not facts:
        return []
    summary = metadata.get("canonical_summary") or content
    topics = {
        EntityResolver.canonicalize(value): value
        for value in admitted_labels(
            metadata, "topics", limit=max_topics, stale_fact_keys=stale_fact_keys
        )
    }
    participants = {
        EntityResolver.canonicalize(value): value
        for value in admitted_labels(
            metadata,
            "participants",
            limit=max_participants,
            stale_fact_keys=stale_fact_keys,
        )
    }
    entities, dropped = declared_structured_entities(content, guarded, stale_fact_keys)
    # 总结抽取直接给出的 metadata["entities"] 也是可绑定目标（节点类型 entity）；
    # 结构化图载荷声明的同名实体优先，保留其声明类型。
    binding_entities = dict(entities)
    for node in metadata_entity_nodes(metadata, stale_fact_keys=stale_fact_keys):
        binding_entities.setdefault(node.canonical_value, node)
    bindings = _explicit_bindings(
        metadata,
        facts,
        summary=summary,
        topics=topics,
        participants=participants,
        entities=binding_entities,
    )
    bindings.extend(
        _structured_bindings(
            guarded,
            facts,
            summary=summary,
            entities=entities,
            dropped_names=dropped,
            stale_fact_keys=stale_fact_keys,
        )
    )
    return bindings


MAX_METADATA_ENTITIES = 8


def metadata_entity_nodes(
    metadata: dict[str, Any] | None,
    *,
    stale_fact_keys: frozenset[str],
) -> list[GraphNode]:
    """把 ``metadata["entities"]`` 规范为 entity 节点（去重、剔除旧事实文本、限量）。

    实体只是独立节点/entry 信号与显式绑定目标，不与事实做笛卡尔积。
    """

    nodes: list[GraphNode] = []
    for value in admitted_labels(
        metadata,
        "entities",
        limit=MAX_METADATA_ENTITIES,
        stale_fact_keys=stale_fact_keys,
    ):
        node = _label_node("entity", value)
        if node is not None:
            nodes.append(node)
    return nodes


def _relation_entry_text(binding: FactBinding) -> str:
    source = binding.source_node.value
    target = binding.target_node.value
    if binding.evidence_kind == EVIDENCE_STRUCTURED_RELATION:
        return f"实体 {source} 与 {target} 的关系为 {binding.relation_type}。"
    if binding.source_node.node_type == "topic":
        return f"主题 {source} 描述了事实 {target}。"
    if binding.source_node.node_type == "person":
        return f"参与者 {source} 与事实 {target} 相关联。"
    return f"实体 {source} 出现在事实 {target} 中。"


def apply_fact_bindings(
    graph: ExtractedGraph,
    source_memory_id: int,
    metadata: dict[str, Any] | None,
    bindings: list[FactBinding],
    *,
    summary: str,
) -> ExtractedGraph:
    """把关系计划写入图快照：补齐端点节点、生成 evidence 边与关系 entry。

    同一来源的同一语义关系只保留一条 evidence（取最高置信度），避免同一来源
    因多种证据重复计数。
    """

    if not bindings:
        return graph
    metadata = metadata or {}
    node_map: dict[str, GraphNode] = {node.node_key: node for node in graph.nodes}
    edges_by_key: dict[str, GraphEdge] = {edge.edge_key: edge for edge in graph.edges}
    entry_keys = {entry.entry_key for entry in graph.entries}
    for binding in bindings:
        for node in (binding.source_node, binding.target_node):
            if node.node_key not in node_map:
                node_map[node.node_key] = node
                graph.nodes.append(node)
        edge = GraphEdge(
            source_key=binding.source_node.node_key,
            target_key=binding.target_node.node_key,
            relation_type=binding.relation_type,
            source_memory_id=source_memory_id,
            confidence=binding.confidence,
            metadata=dict(binding.metadata),
            evidence_kind=binding.evidence_kind,
            binding_key=binding.binding_key,
        )
        existing = edges_by_key.get(edge.edge_key)
        if existing is not None:
            if edge.confidence > existing.confidence:
                existing.confidence = edge.confidence
            continue
        edges_by_key[edge.edge_key] = edge
        graph.edges.append(edge)
        entry_metadata: dict[str, Any] = {
            "source_memory_id": source_memory_id,
            "session_id": metadata.get("session_id"),
            "persona_id": metadata.get("persona_id"),
            "importance": metadata.get("importance", 0.5),
            "create_time": metadata.get("create_time"),
            "last_access_time": metadata.get("last_access_time"),
            "canonical_summary": summary,
            "graph_confidence": binding.confidence,
        }
        if binding.evidence_kind == EVIDENCE_STRUCTURED_RELATION:
            entry_metadata["graph_guardrails_validated"] = True
        payload = (
            f"edge|{source_memory_id}|{binding.relation_type}|"
            f"{edge.source_key}|{edge.target_key}"
        )
        entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        if entry_key in entry_keys:
            continue
        entry_keys.add(entry_key)
        graph.entries.append(
            GraphEntry(
                entry_key=entry_key,
                source_memory_id=source_memory_id,
                session_id=metadata.get("session_id"),
                persona_id=metadata.get("persona_id"),
                entry_type="edge",
                content=_relation_entry_text(binding),
                metadata=entry_metadata,
                node_keys=[edge.source_key, edge.target_key],
                relation_type=binding.relation_type,
            )
        )
    return graph


__all__ = [
    "EVIDENCE_ATOM_CAUSAL",
    "EVIDENCE_ATOM_TEMPORAL",
    "EVIDENCE_FACT_BINDING",
    "EVIDENCE_STRUCTURED_RELATION",
    "FACT_BINDINGS_KEY",
    "FactBinding",
    "admitted_facts",
    "admitted_labels",
    "apply_fact_bindings",
    "declared_structured_entities",
    "is_stale_fact_name",
    "metadata_entity_nodes",
    "plan_fact_bindings",
]
