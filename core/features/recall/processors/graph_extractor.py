"""从已存储的记忆文档中提取图记忆结构。"""

from __future__ import annotations

import hashlib
from typing import Any

from astrbot.api import logger

from ....platform.security.guardrails import (
    GraphExtractionResult,
    validate_llm_response,
)
from ...memory.application.fact_text_alignment import (
    FactTextAlignment,
    fact_in_content,
    facts_aligned,
    normalize_fact,
)
from ...memory.graph.domain.models import (
    ExtractedGraph,
    GraphBoundary,
    GraphEntry,
    GraphNode,
)
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
    filter_atoms_by_current_facts,
    validate_atom_graph_sources,
)
from .entity_resolver import EntityResolver
from .graph_fact_bindings import (
    admitted_labels,
    apply_fact_bindings,
    declared_structured_entities,
    metadata_entity_nodes,
    plan_fact_bindings,
)

# 记录事实表示的字段：事实已不在正文时整体剥离，fact 绑定随事实下标一起失效。
_FACT_TEXT_METADATA_KEYS = frozenset(
    {"key_facts", "fact_source_evidence", "canonical_summary", "fact_bindings"}
)


def _fact_representation_recorded(facts: Any, evidence: Any) -> bool:
    """判断 metadata 是否记录了需要准入判定的事实表示。

    两条回落口径都只针对「已记录的事实表示」：完全没有 ``key_facts`` 与
    ``fact_source_evidence`` 时没有可消费的事实文本，``canonical_summary``
    只由自身规则判定，不按事实回落处理。
    """

    if isinstance(facts, list) and any(
        isinstance(fact, str) and fact.strip() for fact in facts
    ):
        return True
    return isinstance(evidence, list) and bool(evidence)


def _fact_entries_admitted(content: str, facts: Any, evidence: Any) -> bool:
    """判断记录的事实条目是否可进入事实消费（正文成员性 + 三态双口径）。

    逐事实配对证据完整且条目仍属当前正文（``ALIGNED``）时可直接消费；配对证据
    证明条目已失效（``MISALIGNED``）时禁止消费；缺 ``fact_source_evidence`` 的
    历史行（``UNDETERMINABLE``）退回逐条正文成员性判定，每条 ``key_facts`` 文本
    都出现在当前正文中才允许消费。
    """

    alignment = facts_aligned(content, facts, evidence)
    if alignment is FactTextAlignment.ALIGNED:
        return True
    if alignment is FactTextAlignment.MISALIGNED:
        return False
    return (
        isinstance(facts, list)
        and bool(facts)
        and all(fact_in_content(content, fact) for fact in facts)
    )


def _aligned_fact_metadata(
    content: str, metadata: dict[str, Any] | None
) -> dict[str, Any]:
    """按「事实文本单 owner」回落与当前正文不一致的事实元数据。

    事实元数据只有在条目仍出现在当前正文中时才参与图派生：

    1. 记录过事实表示时按三态与正文成员性双重准入，任一条已不在正文中即整体
       剥离（配对证据判定已失效同样剥离）；
    2. ``canonical_summary`` 已不在正文中 → 只丢弃该摘要，改用 canonical 正文。

    两条回落都不丢弃整条记忆：图仍按 canonical 正文派生，只在日志里留下计数
    （不回显正文、事实或身份）。
    """

    if not isinstance(metadata, dict):
        return {}
    facts = metadata.get("key_facts")
    evidence = metadata.get("fact_source_evidence")
    if _fact_representation_recorded(facts, evidence) and not _fact_entries_admitted(
        content, facts, evidence
    ):
        logger.warning(
            "[图提取器] 事实元数据与当前正文不一致，按无事实元数据回落正文：facts=%d",
            len(facts) if isinstance(facts, list) else 0,
        )
        return {
            key: value
            for key, value in metadata.items()
            if key not in _FACT_TEXT_METADATA_KEYS
        }
    summary = metadata.get("canonical_summary")
    if isinstance(summary, str) and summary and not fact_in_content(content, summary):
        logger.debug("[图提取器] 事实摘要与当前正文不一致，改用 canonical 正文作摘要")
        return {
            key: value for key, value in metadata.items() if key != "canonical_summary"
        }
    return metadata


def _stale_fact_keys(content: str, metadata: dict[str, Any] | None) -> frozenset[str]:
    """返回「记录过事实文本、但已不在当前正文」的名字比较键。

    归属判据不看实体自声明的类型：凡是名字等于记录过的事实条目（``key_facts``
    按旧版路径实际消费的 ``str(item)`` 取值、``canonical_summary``）的，就是事实
    表示的载体，无论它出现在 ``key_facts``、legacy ``topics``/``participants``
    还是结构化载荷的实体/关系端点上，都必须仍属于当前 canonical 正文才能建节点、
    生成 entry 或作为关系端点。抽象标签（主题标签、参与者、稳定身份等不来自事实
    表示的字符串）不参与判定，不受回落影响。判定必须在 ``_aligned_fact_metadata``
    剥离事实元数据之前完成，否则记录过的事实文本已不可见。
    """

    if not isinstance(metadata, dict):
        return frozenset()
    recorded: list[Any] = []
    facts = metadata.get("key_facts")
    if isinstance(facts, (list, tuple)):
        recorded.extend(facts)
    summary = metadata.get("canonical_summary")
    if isinstance(summary, str) and summary.strip():
        recorded.append(summary)
    keys: set[str] = set()
    for entry in recorded:
        if not entry:
            continue
        text = str(entry)
        if fact_in_content(content, text):
            continue
        key = normalize_fact(text)
        if key:
            keys.add(key)
    return frozenset(keys)


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
        """根据一条记忆文档构建图快照。

        消费事实元数据前先校验条目仍出现在当前正文中；不一致时按「无事实
        元数据」回落 canonical 正文，不拒绝整条记忆。基于 Atom 构图时还要
        逐条确认 Atom 内容仍属于当前 canonical 事实集合：残留 Atom 不产生
        fact 节点/entry/边，全部残留时同样回落 canonical 正文派生。事实归属
        判据不看实体自声明类型：记录过的事实条目只要已不在当前正文中，无论由
        哪个字段或自声明类型承载，都不产生节点、entry 或关系端点；不来自事实
        表示的抽象标签不受影响。

        永久关系只来自同一份 fact 绑定计划（``graph_fact_bindings``）：Atom、
        结构化与 legacy 三条表示路径只决定节点/entry 信号，关系集合对同一
        canonical 快照保持一致，不因是否加载 Atom 而切换语义。
        """
        stale_fact_keys = _stale_fact_keys(content, metadata)
        metadata = _aligned_fact_metadata(content, metadata)
        GraphBoundary.from_metadata(metadata)
        if atoms:
            validate_atom_graph_sources(source_memory_id, atoms, metadata)
        guarded = self._validate_structured_graph(metadata)
        bindings = plan_fact_bindings(
            content,
            metadata,
            guarded=guarded,
            stale_fact_keys=stale_fact_keys,
            max_facts=self.max_facts,
            max_topics=self.max_topics,
            max_participants=self.max_participants,
        )
        summary = (metadata or {}).get("canonical_summary") or content
        kept_atoms = filter_atoms_by_current_facts(atoms, content, metadata)
        if kept_atoms:
            graph = extract_graph_from_atoms(
                source_memory_id,
                kept_atoms,
                metadata,
                temporal_edges_enabled=self.temporal_edges_enabled,
                causal_edges_enabled=self.causal_edges_enabled,
            )
            return apply_fact_bindings(
                graph, source_memory_id, metadata, bindings, summary=summary
            )
        if guarded is not None:
            graph = self._extract_from_structured_graph(
                source_memory_id,
                content,
                metadata or {},
                guarded,
                stale_fact_keys,
            )
            if graph.entries:
                return apply_fact_bindings(
                    graph, source_memory_id, metadata, bindings, summary=summary
                )
        graph = self._extract_legacy(
            source_memory_id, content, metadata, stale_fact_keys
        )
        return apply_fact_bindings(
            graph, source_memory_id, metadata, bindings, summary=summary
        )

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
            entities = payload.get("entities")
            relations = payload.get("relations")
            if isinstance(entities, list) and isinstance(relations, list):
                try:
                    return GraphExtractionResult(entities=entities, relations=relations)
                except Exception:
                    logger.warning(
                        "[图提取器] 结构化图元数据未通过护栏校验；已回退到旧版提取流程",
                        exc_info=True,
                    )
                    return None
            logger.warning(
                "[图提取器] 结构化图载荷的 entities/relations 必须是数组，"
                "类型=entities:%s/relations:%s",
                type(entities).__name__,
                type(relations).__name__,
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
        stale_fact_keys: frozenset[str],
    ) -> ExtractedGraph:
        """将通过护栏校验的结构化实体转换为节点与检索 entry 信号。

        关系不在这里生成：结构化关系只有带 fact 归属（``fact_index`` 或端点即
        已准入事实）时才由共享绑定计划写入永久边，端点也不再按名字合成节点。
        """
        graph = ExtractedGraph()
        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        summary = metadata.get("canonical_summary") or content

        def _confidence(raw: Any, default: float = 0.75) -> float:
            """将结构化置信度限制在零到一之间。"""
            try:
                return max(0.0, min(1.0, float(raw)))
            except (TypeError, ValueError):
                return default

        # R4.3/D5：事实表示的载体必须属于当前 canonical 正文——归属判据不看
        # 自声明类型（旧事实被贴上 topic/entity 等类型同样是事实文本）。残留
        # 事实不派生节点/entry，也不能作为关系端点；抽象标签不受影响。
        declared, dropped = declared_structured_entities(
            content, guarded, stale_fact_keys
        )
        if dropped:
            logger.debug(
                "[图提取器] 结构化事实实体与当前正文不一致，按无事实回落：entities=%d",
                len(dropped),
            )
        for node in declared.values():
            graph.nodes.append(node)
            payload = (
                f"entity|{source_memory_id}|entity|{node.node_key}|"
                f"实体：{node.value}（类型：{node.node_type}）"
            )
            entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            graph.entries.append(
                GraphEntry(
                    entry_key=entry_key,
                    source_memory_id=source_memory_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    entry_type="entity",
                    content=(
                        f"实体：{node.value}（类型：{node.node_type}）。摘要：{summary}"
                    ),
                    metadata={
                        "source_memory_id": source_memory_id,
                        "session_id": session_id,
                        "persona_id": persona_id,
                        "importance": metadata.get("importance", 0.5),
                        "create_time": metadata.get("create_time"),
                        "last_access_time": metadata.get("last_access_time"),
                        "canonical_summary": summary,
                        "graph_confidence": _confidence(
                            node.metadata.get("confidence"), 0.7
                        ),
                        "graph_guardrails_validated": True,
                    },
                    node_keys=[node.node_key],
                    relation_type="entity",
                )
            )
        return graph

    def _extract_legacy(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
        stale_fact_keys: frozenset[str],
    ) -> ExtractedGraph:
        """从 metadata 生成节点与独立检索 entry（向后兼容路径）。

        topics/participants/key_facts 只各自产生节点与 entry 信号；它们之间不再
        做 topic×fact、participant×fact、participant×participant 的笛卡尔积，
        永久关系只由共享 fact 绑定计划写入。
        """
        metadata = metadata or {}
        graph = ExtractedGraph()

        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        summary = metadata.get("canonical_summary") or content

        def _admitted_names(field: str, limit: int) -> list[str]:
            """读取旧版列表字段，剔除被改写掉的旧事实文本后按上限截断。

            名单字段里的旧事实句子与 ``key_facts`` 里的一样是事实表示的载体：
            归属判据只看名字是否仍是记录过且当前正文仍保有的事实文本，不看它出现
            在哪个字段，也不影响不来自事实表示的抽象标签（主题标签、参与者、稳定
            身份）。
            """

            return admitted_labels(
                metadata, field, limit=limit, stale_fact_keys=stale_fact_keys
            )

        topics = _admitted_names("topics", self.max_topics)
        participants = _admitted_names("participants", self.max_participants)
        key_facts = _admitted_names("key_facts", self.max_facts)

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
        entity_keys: list[str] = []
        for node in metadata_entity_nodes(metadata, stale_fact_keys=stale_fact_keys):
            if node.node_key not in node_map:
                node_map[node.node_key] = node
                entity_keys.append(node.node_key)

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

        for entity_key in entity_keys:
            entity_value = node_map[entity_key].value
            _add_entry(
                "entity",
                f"实体：{entity_value}。摘要：{summary}",
                [entity_key],
                relation_type="entity",
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
