"""图记忆路由内部的混合检索。"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
)
from ..models.memory_atom import compute_decay_score
from ..models.temporal import normalize_datetime
from ..utils.number_utils import clamp_float, safe_float
from .graph_keyword_retriever import GraphKeywordRetriever
from .graph_vector_retriever import GraphVectorRetriever
from .rrf_fusion import BM25Result, RRFFusion, VectorResult


@dataclass(slots=True)
class GraphResult:
    """映射到单条记忆文档的合并图路由结果。"""

    doc_id: int
    final_score: float
    rrf_score: float
    keyword_score: float | None
    vector_score: float | None
    content: str
    metadata: dict[str, Any]
    score_breakdown: dict[str, float] | None = None


class GraphRetriever:
    """融合图关键词检索和图向量检索的结果。"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.GRAPH_RETRIEVER,
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.SCORING,
                AdapterCapability.CANCELLATION,
                AdapterCapability.REFERENCE_TIME,
            }
        ),
        score=ScoreSemantics(
            direction=ScoreDirection.HIGHER_IS_BETTER,
            normalization=NormalizationScope.CALLER,
        ),
    )

    def __init__(
        self,
        keyword_retriever: GraphKeywordRetriever,
        vector_retriever: GraphVectorRetriever,
        rrf_fusion: RRFFusion,
        config: dict[str, Any] | None = None,
    ):
        """装配图关键词/向量路、RRF 和评分配置。"""

        self.keyword_retriever = keyword_retriever
        self.vector_retriever = vector_retriever
        self.rrf_fusion = rrf_fusion
        self.config = config or {}
        self.decay_rate = float(self.config.get("decay_rate", 0.01))
        self.score_alpha = float(self.config.get("graph_memory.score_alpha", 0.55))
        self.score_beta = float(self.config.get("graph_memory.score_beta", 0.2))
        self.score_gamma = float(self.config.get("graph_memory.score_gamma", 0.15))
        self.score_delta = float(self.config.get("graph_memory.score_delta", 0.1))

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        memory_types: list[str] | None = None,
        **kwargs: Any,
    ) -> list[GraphResult]:
        """按请求级参考时间并行执行图关键词检索和图向量检索。"""
        if not query or not query.strip():
            return []

        requested_time = kwargs.get("reference_time")
        if isinstance(requested_time, (int, float)) and not isinstance(
            requested_time, bool
        ):
            current_time = float(requested_time)
        else:
            normalized_time = normalize_datetime(
                requested_time if isinstance(requested_time, datetime) else None
            ) or datetime.now(timezone.utc)
            current_time = normalized_time.timestamp()

        keyword_results, vector_results = await asyncio.gather(
            self.keyword_retriever.search(query, k, session_id, persona_id),
            self.vector_retriever.search(query, k, session_id, persona_id),
        )

        if not keyword_results and not vector_results:
            return []

        fused = self.rrf_fusion.fuse(
            [
                BM25Result(
                    doc_id=item.doc_id,
                    score=item.score,
                    content=item.content,
                    metadata=item.metadata,
                )
                for item in keyword_results
            ],
            [
                VectorResult(
                    doc_id=item.doc_id,
                    score=item.score,
                    content=item.content,
                    metadata=item.metadata,
                )
                for item in vector_results
            ],
            top_k=k,
        )
        if not fused:
            return []

        keyword_score_map = {item.doc_id: item.score for item in keyword_results}
        graph_distance_map = {
            item.doc_id: item.graph_distance for item in keyword_results
        }
        vector_score_map = {item.doc_id: item.score for item in vector_results}

        max_rrf = max(item.rrf_score for item in fused) or 1.0
        results: list[GraphResult] = []

        # 预计算 RELATIONAL 加权条件
        relational_boost = bool(
            memory_types and "relational" in (mt.lower() for mt in memory_types)
        )

        for item in fused:
            metadata = item.metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            importance = clamp_float(metadata.get("importance"), default=0.5)
            create_time = safe_float(metadata.get("create_time"), current_time)
            last_access_time = safe_float(metadata.get("last_access_time"), 0.0)
            freshness_time = max(create_time, last_access_time)
            days_old = max(0.0, (current_time - freshness_time) / 86400)
            recency_weight = math.exp(-self.decay_rate * days_old)
            graph_confidence = clamp_float(
                metadata.get("graph_confidence"), default=0.7
            )
            rrf_normalized = item.rrf_score / max_rrf

            # Atom 图条目使用创建与过期快照，避免把缺失访问时间解释为 Unix 纪元。
            atom_ttl = safe_float(metadata.get("ttl_days"), 0.0)
            expires_at = safe_float(metadata.get("expires_at"), 0.0)
            if expires_at > 0 and current_time >= expires_at:
                continue
            temporal_factor = 1.0
            decay_type = str(metadata.get("decay_type", ""))
            if atom_ttl > 0:
                days_since_creation = max(
                    0.0,
                    (current_time - create_time) / 86400.0,
                )
                temporal_factor = compute_decay_score(
                    decay_type,
                    atom_ttl,
                    days_since_creation,
                )

            final_score = (
                self.score_alpha * rrf_normalized
                + self.score_beta * importance
                + self.score_gamma * recency_weight
                + self.score_delta * graph_confidence
            ) * temporal_factor

            # RELATIONAL 类型加权
            if relational_boost:
                relation_type = metadata.get("graph_relation_type", "")
                if relation_type:
                    final_score *= 1.3

            score_breakdown = {
                "graph_rrf_normalized": round(rrf_normalized, 4),
                "graph_importance": round(importance, 4),
                "graph_recency_weight": round(recency_weight, 4),
                "graph_confidence": round(graph_confidence, 4),
                "graph_temporal_factor": round(temporal_factor, 4),
                "graph_final_score": round(final_score, 4),
            }
            graph_distance = graph_distance_map.get(item.doc_id)
            if graph_distance is not None:
                score_breakdown["graph_min_distance"] = float(graph_distance)

            results.append(
                GraphResult(
                    doc_id=item.doc_id,
                    final_score=final_score,
                    rrf_score=item.rrf_score,
                    keyword_score=keyword_score_map.get(item.doc_id),
                    vector_score=vector_score_map.get(item.doc_id),
                    content=item.content,
                    metadata=metadata,
                    score_breakdown=score_breakdown,
                )
            )

        results.sort(key=lambda item: item.final_score, reverse=True)
        return results[:k]


__all__ = ["GraphRetriever", "GraphResult"]
