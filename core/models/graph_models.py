"""插件使用的图记忆数据模型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNode:
    """图记忆层中的规范节点。"""

    node_type: str
    value: str
    canonical_value: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        return f"{self.node_type}:{self.canonical_value}"


@dataclass(slots=True)
class GraphEdge:
    """从一份记忆文档中提取的图边。"""

    source_key: str
    target_key: str
    relation_type: str
    source_memory_id: int
    confidence: float = 0.8
    weight: float = 1.0
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def edge_key(self) -> str:
        return (
            f"{self.source_key}|{self.relation_type}|"
            f"{self.target_key}|{self.source_memory_id}"
        )

    @property
    def semantic_edge_key(self) -> str:
        """跨记忆边标识，忽略 source_memory_id。"""
        return f"{self.source_key}|{self.relation_type}|{self.target_key}"


@dataclass(slots=True)
class GraphEntry:
    """映射回某份记忆文档的可搜索图产物。"""

    entry_key: str
    source_memory_id: int
    session_id: str | None
    persona_id: str | None
    entry_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    node_keys: list[str] = field(default_factory=list)
    relation_type: str | None = None


@dataclass(slots=True)
class ExtractedGraph:
    """从一份记忆文档中提取的结构化图快照。"""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    entries: list[GraphEntry] = field(default_factory=list)


__all__ = ["GraphNode", "GraphEdge", "GraphEntry", "ExtractedGraph"]
