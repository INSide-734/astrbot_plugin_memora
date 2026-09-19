"""图记忆路由内部的混合检索。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from astrbot.api import logger

from ...shared.adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
)
from ...shared.data_helpers import safe_parse_metadata
from ...shared.memory_status import is_memory_recallable
from ...shared.number_utils import clamp_float, safe_float
from ...shared.temporal import normalize_datetime
from ..memory.domain.memory_atom import compute_decay_score
from ..memory.domain.revision import memory_revision
from ..memory.graph.domain.models import (
    GraphBoundary,
    GraphQueryScope,
    resolve_graph_query_scope,
)
from ..quality.application.gate_disposition_filter import is_mark_write
from .graph_keyword_retriever import GraphKeywordRetriever
from .graph_vector_retriever import GraphVectorRetriever
from .rrf_fusion import BM25Result, RRFFusion, VectorResult
from .vector_deadline import run_local_and_bounded_vector

# 图条目自带的衰减注解；canonical metadata 不含这些派生评分字段。
_SAFE_GRAPH_METADATA_KEYS: Final = frozenset({"ttl_days", "expires_at", "decay_type"})

# canonical 校验发生在图路线返回之后；只做有界补位，不承诺无限填充。
_SOURCE_VALIDATION_OVERFETCH: Final = 16


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


@dataclass(frozen=True, slots=True)
class _CanonicalSource:
    """通过 source boundary 校验的当前 canonical 快照。"""

    boundary: GraphBoundary
    content: str
    metadata: dict[str, Any]


def _canonical_result_metadata(
    canonical_metadata: dict[str, Any],
    graph_metadata: dict[str, Any],
) -> dict[str, Any]:
    """以 canonical metadata 为底，只保留图路评分所需的安全注解。"""

    merged = dict(canonical_metadata)
    for key, value in graph_metadata.items():
        if key in _SAFE_GRAPH_METADATA_KEYS or key.startswith("graph_"):
            merged[key] = value
    return merged


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
        *,
        memory_loader: Callable[[int], Awaitable[dict[str, Any] | None]] | None = None,
    ):
        """装配图关键词/向量路、RRF、评分配置与 canonical 来源校验器。"""

        self.keyword_retriever = keyword_retriever
        self.vector_retriever = vector_retriever
        self.rrf_fusion = rrf_fusion
        self.memory_loader = memory_loader
        self.config = config or {}
        self.decay_rate = float(self.config.get("decay_rate", 0.01))
        self.score_alpha = float(self.config.get("graph_memory.score_alpha", 0.55))
        self.score_beta = float(self.config.get("graph_memory.score_beta", 0.2))
        self.score_gamma = float(self.config.get("graph_memory.score_gamma", 0.15))
        self.score_delta = float(self.config.get("graph_memory.score_delta", 0.1))

    async def _search_route(
        self, awaitable: Awaitable[list[Any]]
    ) -> tuple[list[Any], bool, float]:
        """执行单条图路并返回结果、失败标志和真实耗时。"""
        started = time.perf_counter()
        try:
            results = await awaitable
            return results, False, (time.perf_counter() - started) * 1000.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[GraphRetriever] 单路检索降级，异常类型=%s", exc.__class__.__name__
            )
            return [], True, (time.perf_counter() - started) * 1000.0

    async def _load_canonical_sources(
        self, doc_ids: list[int]
    ) -> dict[int, _CanonicalSource | None]:
        """在本调用内并发加载每个来源的当前 canonical 快照。"""

        loaded: dict[int, _CanonicalSource | None] = {}
        loader = self.memory_loader
        if loader is None or not doc_ids:
            return loaded
        results = await asyncio.gather(
            *(loader(doc_id) for doc_id in doc_ids),
            return_exceptions=True,
        )
        for doc_id, memory in zip(doc_ids, results, strict=True):
            if isinstance(memory, asyncio.CancelledError):
                raise memory
            loaded[doc_id] = (
                None
                if isinstance(memory, BaseException)
                else self._canonical_source(memory)
            )
        return loaded

    async def _validate_canonical_hits(
        self,
        keyword_results: list[Any],
        vector_results: list[Any],
        *,
        scope: GraphQueryScope,
        revision_token: str | None,
    ) -> tuple[list[Any], list[Any], dict[int, _CanonicalSource]]:
        """按每条命中自带的 source boundary 过滤两条图路，再交给 RRF。

        命中必须先落在本次请求 scope 内，并在提供精确 revision 时携带同一
        revision；随后才允许与来源自身的当前 canonical 边界比较。
        """

        candidates = [*keyword_results, *vector_results]
        sources = await self._load_canonical_sources(
            sorted({int(item.doc_id) for item in candidates})
        )
        validated = {
            doc_id: source for doc_id, source in sources.items() if source is not None
        }

        def matches(item: Any) -> bool:
            source = validated.get(int(item.doc_id))
            proof = item.source_boundary
            if source is None or not isinstance(proof, GraphBoundary):
                return False
            if (
                proof.scope_key != scope.scope_key
                or proof.privacy_level != scope.privacy_level
            ):
                return False
            if revision_token is not None and proof.revision_token != revision_token:
                return False
            return proof == source.boundary

        return (
            [item for item in keyword_results if matches(item)],
            [item for item in vector_results if matches(item)],
            validated,
        )

    @staticmethod
    def _canonical_source(memory: Any) -> _CanonicalSource | None:
        """把 canonical 记录收敛为可校验来源；缺失 canonical 证据时返回 None。"""

        if not isinstance(memory, dict):
            return None
        raw_metadata = memory.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = dict(raw_metadata)
        elif isinstance(raw_metadata, str) and raw_metadata:
            metadata = safe_parse_metadata(raw_metadata)
        else:
            return None
        if not is_memory_recallable(metadata) or is_mark_write(metadata):
            return None
        revision = memory_revision(memory)
        if not revision:
            return None
        try:
            boundary = GraphBoundary.from_metadata(
                {
                    "scope_key": metadata.get("scope_key"),
                    "privacy_level": metadata.get("privacy_level"),
                    "revision_token": revision,
                }
            )
        except ValueError:
            return None
        return _CanonicalSource(
            boundary=boundary,
            content=str(memory.get("text") or ""),
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        memory_types: list[str] | None = None,
        timing_sink: dict[str, object] | None = None,
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
        **kwargs: Any,
    ) -> list[GraphResult]:
        """按请求级参考时间并行执行图关键词检索和图向量检索。

        配置 ``memory_loader`` 时，两条图路的命中必须携带落在请求 scope 内、且与
        来源当前 canonical 边界一致的 source boundary（显式 boundary 还要求同一
        revision），才会进入 RRF 融合与 top-k 截断。
        """
        if boundary is None and query_scope is None:
            # 无请求级 scope 时不构造任何图查询；显式跳过并保持调用方可观测。
            if timing_sink is not None:
                timing_sink["graph_route_skipped"] = True
            return []
        # 请求 scope 必须唯一且合法；冲突或类型错误在此静态失败，而不是静默降级。
        resolved_scope, resolved_revision = resolve_graph_query_scope(
            boundary=boundary,
            query_scope=query_scope,
        )
        if query_scope is not None and self.memory_loader is None:
            # 请求 scope 路径无法校验命中来源时必须跳过，而不是把图结果当成授权证据。
            if timing_sink is not None:
                timing_sink["graph_route_skipped"] = True
            return []
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

        _t_graph_start = time.perf_counter()
        deadline_monotonic = kwargs.get("deadline_monotonic")
        route_k = max(
            k,
            min(max(k, 1) * 3, max(k, 1) + _SOURCE_VALIDATION_OVERFETCH),
        )
        (
            local_route,
            vector_route,
            vector_timed_out,
        ) = await run_local_and_bounded_vector(
            lambda: self._search_route(
                self.keyword_retriever.search(
                    query,
                    route_k,
                    session_id,
                    persona_id,
                    boundary=boundary,
                    query_scope=query_scope,
                ),
            ),
            lambda: self._search_route(
                self.vector_retriever.search(
                    query,
                    route_k,
                    session_id,
                    persona_id,
                    boundary=boundary,
                    query_scope=query_scope,
                ),
            ),
            deadline_monotonic=(
                float(deadline_monotonic)
                if isinstance(deadline_monotonic, (int, float))
                and not isinstance(deadline_monotonic, bool)
                else None
            ),
        )
        keyword_results, keyword_failed, _kw_ms = local_route
        if vector_route is None:
            vector_results, vector_failed, _vec_ms = [], True, 0.0
        else:
            vector_results, vector_failed, _vec_ms = vector_route
        if timing_sink is not None:
            timing_sink["graph_keyword_ms"] = _kw_ms
            timing_sink["graph_vector_ms"] = _vec_ms
            if vector_timed_out:
                timing_sink.update(
                    {
                        "graph_vector_timed_out": True,
                        "deadline_exhausted": True,
                        "partial_fallback": bool(keyword_results),
                    }
                )

        canonical_sources: dict[int, _CanonicalSource] = {}
        if self.memory_loader is not None:
            raw_candidate_count = len(keyword_results) + len(vector_results)
            # 命中必须先落在请求 scope 内并证明仍属于当前 canonical 来源，才能融合。
            (
                keyword_results,
                vector_results,
                canonical_sources,
            ) = await self._validate_canonical_hits(
                keyword_results,
                vector_results,
                scope=resolved_scope,
                revision_token=resolved_revision,
            )
            validated_count = len(keyword_results) + len(vector_results)
            if timing_sink is not None and raw_candidate_count > validated_count:
                timing_sink["graph_candidates_rejected"] = (
                    raw_candidate_count - validated_count
                )
                if validated_count == 0:
                    timing_sink["graph_route_exhausted"] = True
        if not keyword_results and not vector_results:
            if timing_sink is not None:
                if keyword_failed and vector_failed:
                    timing_sink["graph_route_degraded"] = True
                timing_sink["graph_fusion_ms"] = 0.0
                timing_sink["graph_total_ms"] = (
                    time.perf_counter() - _t_graph_start
                ) * 1000.0
            return []

        _t_fusion_start = time.perf_counter()
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
            if timing_sink is not None:
                timing_sink["graph_fusion_ms"] = 0.0
                timing_sink["graph_total_ms"] = (
                    time.perf_counter() - _t_graph_start
                ) * 1000.0
            return []
        _t_fusion_end = time.perf_counter()
        if timing_sink is not None:
            timing_sink["graph_fusion_ms"] = (_t_fusion_end - _t_fusion_start) * 1000.0

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

            content = item.content
            canonical = canonical_sources.get(item.doc_id)
            if canonical is not None:
                # 校验通过后正文与用户 metadata 以 canonical 为准，只保留图路注解。
                content = canonical.content
                metadata = _canonical_result_metadata(canonical.metadata, metadata)

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
                    content=content,
                    metadata=metadata,
                    score_breakdown=score_breakdown,
                )
            )

        results.sort(key=lambda item: item.final_score, reverse=True)
        _t_graph_end = time.perf_counter()
        if timing_sink is not None:
            timing_sink["graph_total_ms"] = (_t_graph_end - _t_graph_start) * 1000.0
        return results[:k]


__all__ = ["GraphRetriever", "GraphResult"]
