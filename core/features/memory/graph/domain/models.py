"""插件使用的图记忆数据模型。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphBoundary:
    """canonical 作用域、隐私与来源修订快照，不从显示名推断。"""

    scope_key: str
    privacy_level: str
    revision_token: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.scope_key, self.privacy_level, self.revision_token)
        ):
            raise ValueError("graph_boundary_required")
        if self.privacy_level not in {"public", "shared", "confidential"}:
            raise ValueError("graph_boundary_required")

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> "GraphBoundary":
        if not isinstance(metadata, Mapping):
            raise ValueError("graph_boundary_required")
        scope_key = metadata.get("scope_key")
        privacy_level = metadata.get("privacy_level")
        revision_token = metadata.get("revision_token")
        if (
            not isinstance(scope_key, str)
            or not isinstance(privacy_level, str)
            or not isinstance(revision_token, str)
        ):
            raise ValueError("graph_boundary_required")
        return cls(scope_key, privacy_level, revision_token)

    @staticmethod
    def require(boundary: object) -> "GraphBoundary":
        if not isinstance(boundary, GraphBoundary):
            raise ValueError("graph_boundary_required")
        return boundary

    def as_params(self) -> dict[str, str]:
        return {
            "scope_key": self.scope_key,
            "privacy_level": self.privacy_level,
            "revision_token": self.revision_token,
        }

    def validate_metadata(self, metadata: Mapping[str, Any]) -> None:
        """拒绝相互冲突的内部快照，不补造缺失字段。"""
        for key, value in self.as_params().items():
            if key in metadata and metadata[key] != value:
                raise ValueError("graph_boundary_mismatch")


@dataclass(frozen=True, slots=True)
class GraphQueryScope:
    """请求授权范围；来源修订属于各个对象，不属于查询整体。"""

    scope_key: str
    privacy_level: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope_key, str) or not self.scope_key.strip():
            raise ValueError("graph_query_scope_required")
        if not isinstance(self.privacy_level, str) or self.privacy_level not in {
            "public",
            "shared",
            "confidential",
        }:
            raise ValueError("graph_query_scope_required")

    @staticmethod
    def require(scope: object) -> "GraphQueryScope":
        if not isinstance(scope, GraphQueryScope):
            raise ValueError("graph_query_scope_required")
        return scope

    @classmethod
    def from_boundary(cls, boundary: GraphBoundary) -> "GraphQueryScope":
        """仅保留来源已验证的作用域与隐私，不继承对象级 revision。"""
        return cls(boundary.scope_key, boundary.privacy_level)

    def as_params(self) -> dict[str, str]:
        return {
            "scope_key": self.scope_key,
            "privacy_level": self.privacy_level,
        }


def resolve_graph_query_scope(
    *,
    boundary: GraphBoundary | None,
    query_scope: GraphQueryScope | None,
) -> tuple[GraphQueryScope, str | None]:
    """解析精确来源边界或不限定单一 revision 的请求范围。"""
    if boundary is not None and query_scope is not None:
        raise ValueError("graph_query_scope_conflict")
    if boundary is not None:
        resolved = GraphBoundary.require(boundary)
        return GraphQueryScope.from_boundary(resolved), resolved.revision_token
    if query_scope is None:
        raise ValueError("graph_query_scope_required")
    return GraphQueryScope.require(query_scope), None


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


__all__ = [
    "GraphBoundary",
    "GraphQueryScope",
    "resolve_graph_query_scope",
    "GraphNode",
    "GraphEdge",
    "GraphEntry",
    "ExtractedGraph",
]
