"""Recall Trace 内部模型与安全 DTO 序列化入口。"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, field
from enum import Enum
from typing import Any


JsonDict = dict[str, Any]


def json_safe(value: Any) -> Any:
    """返回递归可 JSON 序列化的内部副本；该函数本身不提供脱敏。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, Enum):
        return json_safe(value.value)

    if isinstance(value, os.PathLike):
        return os.fspath(value)

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())

    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: json_safe(getattr(value, item.name))
            for item in fields(value)
        }

    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, set | frozenset):
        return sorted((json_safe(item) for item in value), key=str)

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_safe(item) for item in value]

    return str(value)


_json_safe = json_safe


@dataclass(slots=True)
class TraceStage:
    """描述一个召回追踪阶段的内部数据。"""

    name: str
    duration_ms: float = 0.0
    candidate_count: int = 0
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        """转换为待统一脱敏的内部映射。"""
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "candidate_count": self.candidate_count,
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class ScoreContribution:
    """描述一个排序分数来源的内部数据。"""

    source: str
    score: float
    weight: float = 1.0
    explanation: str | None = None

    def to_dict(self) -> JsonDict:
        """转换为待统一脱敏的内部映射。"""
        return {
            "source": self.source,
            "score": self.score,
            "weight": self.weight,
            "explanation": self.explanation,
        }


@dataclass(slots=True)
class GraphProvenancePath:
    """描述内部图路径；节点和边不得进入最终安全 DTO。"""

    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    score: float | None = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        """转换为待统一脱敏的内部映射。"""
        return {
            "nodes": _json_safe(self.nodes),
            "edges": _json_safe(self.edges),
            "score": self.score,
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class TraceResult:
    """描述单个 canonical 候选的内部追踪结果。"""

    doc_id: str
    rank: int
    initial_score: float
    final_score: float
    score_contributions: list[ScoreContribution] = field(default_factory=list)
    graph_paths: list[GraphProvenancePath] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        """转换为待统一脱敏的内部映射。"""
        return {
            "doc_id": self.doc_id,
            "rank": self.rank,
            "initial_score": self.initial_score,
            "final_score": self.final_score,
            "score_contributions": [
                item.to_dict() for item in self.score_contributions
            ],
            "graph_paths": [item.to_dict() for item in self.graph_paths],
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class FilteredCandidate:
    """描述一个被过滤候选的内部原因。"""

    doc_id: str
    reason: str
    stage: str | None = None
    score: float | None = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        """转换为待统一脱敏的内部映射。"""
        return {
            "doc_id": self.doc_id,
            "reason": self.reason,
            "stage": self.stage,
            "score": self.score,
            "metadata": _json_safe(self.metadata),
        }


@dataclass(slots=True)
class RecallTrace:
    """聚合一次召回的内部追踪数据。"""

    trace_id: str
    query: str
    total_ms: float
    stages: list[TraceStage] = field(default_factory=list)
    results: list[TraceResult] = field(default_factory=list)
    filtered: list[FilteredCandidate] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        """返回移除查询、正文、身份和 canonical ID 的安全 DTO。"""
        from .trace_privacy import sanitize_trace_payload

        internal = {
            "trace_id": self.trace_id,
            "query": self.query,
            "total_ms": self.total_ms,
            "stages": [item.to_dict() for item in self.stages],
            "results": [item.to_dict() for item in self.results],
            "filtered": [item.to_dict() for item in self.filtered],
            "created_at": self.created_at,
            "metadata": _json_safe(self.metadata),
        }
        return sanitize_trace_payload(internal)


__all__ = [
    "FilteredCandidate",
    "GraphProvenancePath",
    "RecallTrace",
    "ScoreContribution",
    "TraceResult",
    "TraceStage",
    "json_safe",
]
