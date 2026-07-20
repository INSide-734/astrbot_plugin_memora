"""将文档检索路由与图检索路由融合为单一结果列表。"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..models.memory_evolution import ExpansionBudget, ScopeContext
from ..models.temporal import normalize_datetime
from ..models.recall_strategy import RecallStrategy
from .graph_retriever import GraphRetriever
from .hybrid_retriever import HybridRetriever
from .intent_keywords import FACTUAL_TERMS, RELATION_TERMS, TEMPORAL_TERMS
from .projection_reader import ProjectionBudget, ProjectionScope
from .rrf_fusion import HybridResult

if TYPE_CHECKING:
    from .query_rewriter import QueryIntent


class DualRouteRetriever:
    """协调文档检索路由与图检索路由。"""

    def __init__(
        self,
        document_retriever: HybridRetriever,
        graph_retriever: GraphRetriever,
        memory_loader: Callable[[int], Awaitable[dict[str, Any] | None]],
        config: dict[str, Any] | None = None,
        personalized_ranker=None,
        profile_manager=None,
        reranker=None,
        derived_expander=None,
        projection_reader=None,
    ):
        self.document_retriever = document_retriever
        self.graph_retriever = graph_retriever
        self.memory_loader = memory_loader
        self.config = config or {}
        self.document_route_weight = float(
            self.config.get("document_route_weight", 0.65)
        )
        self.graph_route_weight = float(self.config.get("graph_route_weight", 0.35))
        self.cross_route_bonus = float(self.config.get("cross_route_bonus", 0.08))
        self.dynamic_route_weighting = bool(
            self.config.get("dynamic_route_weighting", True)
        )
        # v2.5: 个性化排序
        self.personalized_ranker = personalized_ranker
        self.profile_manager = profile_manager
        # v2.5: 可插拔重排序器（MMR / Cross-Encoder / LLM / Hybrid）
        self.reranker = reranker
        self.derived_expander = derived_expander
        self.projection_reader = projection_reader
        self._reranker_strategy = self.config.get("reranker.strategy", "mmr")
        # 阶段计时存储（每次 search() 后更新）
        self.last_search_timing: dict[str, float] = {}

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        strategy: RecallStrategy | None = None,
        memory_types: list[str] | None = None,
        chat_type: str = "private",
        query_intent: QueryIntent | None = None,
        user_id: str | None = None,
        reference_time: datetime | None = None,
    ) -> list[HybridResult]:
        """同时运行两条检索路由，并合并候选记忆。

        参数:
            chat_type: "private" or "group" — 用于隐私过滤。
            query_intent: R1 查询改写结果，优先使用 LLM 意图做权重调整。
            user_id: v2.5 用户 ID，用于个性化排序。
        """
        reference_time = normalize_datetime(reference_time) or datetime.now(timezone.utc)
        _t_route_start = time.perf_counter()
        doc_task = self.document_retriever.search(
            query,
            max(k * 2, k),
            session_id,
            persona_id,
            memory_types=memory_types,
        )
        graph_task = self.graph_retriever.search(
            query,
            max(k * 2, k),
            session_id,
            persona_id,
            memory_types=memory_types,
        )
        _t_doc_start = time.perf_counter()
        doc_results = await doc_task
        _t_doc_end = time.perf_counter()
        graph_results = await graph_task
        _t_graph_end = time.perf_counter()
        document_route_ms = (_t_doc_end - _t_doc_start) * 1000.0
        graph_route_ms = (_t_graph_end - _t_doc_end) * 1000.0

        _t_merge_start = time.perf_counter()
        if not graph_results:
            merged = list(doc_results)
        else:
            merged = await self._merge_dual_results(
                doc_results,
                graph_results,
                query,
                strategy=strategy,
                query_intent=query_intent,
            )
        merge_ms = (time.perf_counter() - _t_merge_start) * 1000.0

        # 人格感知记忆解读 — 当前 persona 匹配的记忆获得加权
        if persona_id and merged:
            merged = self._apply_persona_boost(merged, persona_id)

        # v2.5 个性化排序 — 基于用户画像标签加权
        if user_id and self.personalized_ranker and self.profile_manager and merged:
            try:
                tag_weights = await self.profile_manager.get_tag_weights(user_id)
                profile = await self.profile_manager.get_profile(user_id)
                merged = self.personalized_ranker.apply(merged, tag_weights, profile)
            except Exception:
                pass  # 个性化排序失败不影响主流程

        _t_expansion_start = time.perf_counter()
        evolution_config = self.config.get("memory_evolution", {})
        if not isinstance(evolution_config, dict):
            evolution_config = {}
        if (
            self.derived_expander is not None
            and bool(evolution_config.get("enabled", False))
            and str(evolution_config.get("mode", "disabled")) in {"readonly", "active"}
            and merged
        ):
            baseline = list(merged)
            try:
                max_expansions = max(
                    0,
                    int(evolution_config.get("max_query_expansions", 8)),
                )
                merged = await self.derived_expander.expand(
                    merged,
                    scope=ScopeContext(
                        scope_key=session_id or persona_id or f"{chat_type}:default",
                        privacy_level=(
                            "shared" if chat_type == "group" else "confidential"
                        ),
                    ),
                    budget=ExpansionBudget(
                        max_chars=max(
                            0,
                            int(
                                evolution_config.get(
                                    "projection_budget_chars",
                                    2_000,
                                )
                            ),
                        ),
                        max_items=len(merged) + max_expansions,
                    ),
                    reference_time=reference_time,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                merged = baseline

        if (
            self.projection_reader is not None
            and bool(evolution_config.get("enabled", False))
            and str(evolution_config.get("mode", "disabled")) in {"readonly", "active"}
            and merged
        ):
            baseline = list(merged)
            try:
                projection_budget_chars = max(
                    0,
                    int(evolution_config.get("projection_budget_chars", 2_000)),
                )
                projection_limit = max(
                    0,
                    int(evolution_config.get("max_query_expansions", 8)),
                )
                merged = await self.projection_reader.attach(
                    merged,
                    scope=ProjectionScope(
                        scope_key=session_id or persona_id or f"{chat_type}:default",
                        privacy_level=(
                            "shared" if chat_type == "group" else "confidential"
                        ),
                        now=reference_time,
                        reference_time=reference_time,
                    ),
                    budget=ProjectionBudget(
                        max_chars=projection_budget_chars,
                        max_items=projection_limit,
                        max_per_candidate=4,
                        max_summary_chars=min(600, projection_budget_chars),
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                merged = baseline
        expansion_ms = (time.perf_counter() - _t_expansion_start) * 1000.0

        # v2.5 可插拔重排序 — MMR / Cross-Encoder / LLM / Hybrid
        _t_rerank_start = time.perf_counter()
        if self.reranker and len(merged) > 1:
            merged = await self._apply_reranker(merged, k, query=query)
        else:
            merged.sort(key=lambda item: item.final_score, reverse=True)
        rerank_ms = (time.perf_counter() - _t_rerank_start) * 1000.0

        # 存储阶段计时
        self.last_search_timing = {
            "document_route_ms": document_route_ms,
            "graph_route_ms": graph_route_ms,
            "merge_ms": merge_ms,
            "derived_expansion_ms": expansion_ms,
            "rerank_ms": rerank_ms,
        }

        return self._filter_by_privacy(merged[:k], chat_type)

    async def _apply_reranker(
        self,
        results: list[HybridResult],
        k: int,
        *,
        query: str,
    ) -> list[HybridResult]:
        """兼容同步和异步重排序器，失败时保持基础召回结果。"""
        fallback = list(results)
        fallback.sort(key=lambda item: item.final_score, reverse=True)
        try:
            reranked = self.reranker.rerank(results, k, query=query)
            if inspect.isawaitable(reranked):
                reranked = await reranked
            if not isinstance(reranked, list):
                return fallback
            return reranked
        except Exception:
            return fallback

    @staticmethod
    def _filter_by_privacy(
        results: list[HybridResult],
        chat_type: str,
    ) -> list[HybridResult]:
        """群聊场景过滤机密记忆（私聊秘密不在群聊暴露）。

        向后兼容：没有 privacy_level 的记忆视为 "shared"（都可访问）。
        """
        if chat_type != "group":
            return results
        return [
            r
            for r in results
            if (r.metadata or {}).get("privacy_level", "shared") != "confidential"
        ]

    def _apply_persona_boost(
        self,
        results: list[HybridResult],
        persona_id: str,
    ) -> list[HybridResult]:
        """人格感知记忆解读 — 匹配当前 persona 的记忆获得 boost 加权。"""
        if not self.config.get("persona_interpretation.enabled", False):
            return results
        boost = float(self.config.get("persona_interpretation.boost", 1.2))
        for r in results:
            meta = r.metadata or {}
            interpretations = meta.get("persona_interpretations", {}) or {}
            if isinstance(interpretations, dict) and persona_id in interpretations:
                r.final_score = min(1.0, r.final_score * boost)
        return results

    async def _merge_dual_results(
        self,
        doc_results: list[HybridResult],
        graph_results: list[HybridResult],
        query: str,
        strategy: RecallStrategy | None = None,
        query_intent: QueryIntent | None = None,
    ) -> list[HybridResult]:
        if strategy is not None:
            document_weight, graph_weight = self._compute_strategy_weights(strategy)
            intent = "strategy"
        elif query_intent is not None:
            document_weight, graph_weight, intent = self._route_weights_for_query(
                query,
                query_intent=query_intent,
            )
        else:
            document_weight, graph_weight, intent = self._route_weights_for_query(query)

        document_max = (
            max((item.final_score for item in doc_results), default=1.0) or 1.0
        )
        graph_max = (
            max((item.final_score for item in graph_results), default=1.0) or 1.0
        )

        doc_map = {item.doc_id: item for item in doc_results}
        graph_map = {item.doc_id: item for item in graph_results}
        all_doc_ids = set(doc_map) | set(graph_map)

        # 阶段 1：找出需要通过 memory_loader 回填的 doc_id
        need_load_ids: list[int] = []
        for doc_id in all_doc_ids:
            doc_result = doc_map.get(doc_id)
            mem_content = doc_result.content if doc_result is not None else ""
            mem_meta = (
                dict(doc_result.metadata)
                if doc_result is not None and isinstance(doc_result.metadata, dict)
                else {}
            )
            if not mem_content or not mem_meta:
                need_load_ids.append(doc_id)

        # 阶段 2：并行批量加载缺失记忆（N+1 -> 1）
        loaded: dict[int, dict[str, Any] | None] = {}
        if need_load_ids:
            loaded_list = await asyncio.gather(
                *(self.memory_loader(doc_id) for doc_id in need_load_ids),
                return_exceptions=True,
            )
            for doc_id, mem in zip(need_load_ids, loaded_list, strict=False):
                if isinstance(mem, BaseException) or mem is None:
                    loaded[doc_id] = None
                else:
                    loaded[doc_id] = mem

        # 阶段 3：构建合并结果
        merged_results: list[HybridResult] = []
        for doc_id in all_doc_ids:
            doc_result = doc_map.get(doc_id)
            graph_result = graph_map.get(doc_id)

            doc_signal = (
                doc_result.final_score / document_max if doc_result is not None else 0.0
            )
            graph_signal = (
                graph_result.final_score / graph_max
                if graph_result is not None
                else 0.0
            )
            route_bonus = (
                self.cross_route_bonus
                if doc_result is not None and graph_result is not None
                else 0.0
            )

            memory_content = doc_result.content if doc_result is not None else ""
            memory_metadata = (
                dict(doc_result.metadata)
                if doc_result is not None and isinstance(doc_result.metadata, dict)
                else {}
            )

            if not memory_content or not memory_metadata:
                memory = loaded.get(doc_id)
                if not memory:
                    continue
                memory_content = str(memory.get("text") or memory_content)
                raw_metadata = memory.get("metadata") or memory_metadata
                memory_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

            final_score = min(
                1.0,
                document_weight * doc_signal
                + graph_weight * graph_signal
                + route_bonus,
            )

            score_breakdown = self._build_score_breakdown(
                doc_result=doc_result,
                graph_result=graph_result,
                doc_signal=doc_signal,
                graph_signal=graph_signal,
                document_weight=document_weight,
                graph_weight=graph_weight,
                route_bonus=route_bonus,
                final_score=final_score,
                intent=intent,
            )

            merged_results.append(
                HybridResult(
                    doc_id=doc_id,
                    final_score=final_score,
                    rrf_score=max(
                        doc_result.rrf_score if doc_result is not None else 0.0,
                        graph_result.rrf_score if graph_result is not None else 0.0,
                    ),
                    bm25_score=doc_result.bm25_score
                    if doc_result is not None
                    else None,
                    vector_score=(
                        doc_result.vector_score if doc_result is not None else None
                    ),
                    content=memory_content,
                    metadata=memory_metadata,
                    score_breakdown=score_breakdown,
                )
            )

        return merged_results

    @staticmethod
    def _build_score_breakdown(
        *,
        doc_result: HybridResult | None,
        graph_result: Any | None,
        doc_signal: float,
        graph_signal: float,
        document_weight: float,
        graph_weight: float,
        route_bonus: float,
        final_score: float,
        intent: str,
    ) -> dict[str, float]:
        score_breakdown: dict[str, float] = {}
        if doc_result and doc_result.score_breakdown:
            score_breakdown.update(doc_result.score_breakdown)
        if graph_result and graph_result.score_breakdown:
            score_breakdown.update(graph_result.score_breakdown)
        score_breakdown.update(
            {
                "document_route_score": round(doc_signal, 4),
                "graph_route_score": round(graph_signal, 4),
                "document_route_weight": round(document_weight, 4),
                "graph_route_weight": round(graph_weight, 4),
                "cross_route_bonus": round(route_bonus, 4),
                "dual_route_final_score": round(final_score, 4),
            }
        )
        if intent:
            score_breakdown["query_intent"] = intent
        if doc_result is not None:
            score_breakdown["document_keyword_score"] = round(
                float(doc_result.bm25_score or 0.0), 4
            )
            score_breakdown["document_vector_score"] = round(
                float(doc_result.vector_score or 0.0), 4
            )
        if graph_result is not None:
            score_breakdown["graph_keyword_score"] = round(
                float(graph_result.keyword_score or 0.0), 4
            )
            score_breakdown["graph_vector_score"] = round(
                float(graph_result.vector_score or 0.0), 4
            )
        return score_breakdown

    def _route_weights_for_query(
        self,
        query: str,
        query_intent: QueryIntent | None = None,
    ) -> tuple[float, float, str]:
        """根据 LLM 意图（R1）或关键词降级结果调整文档/图路权重。"""
        base_document = self.document_route_weight
        base_graph = self.graph_route_weight
        if not self.dynamic_route_weighting:
            return base_document, base_graph, "fixed"

        # R1: 优先使用 LLM 意图
        intent = "default"
        if query_intent is not None and query_intent.intent != "default":
            intent = query_intent.intent
            if intent == "relationship":
                return (
                    max(0.15, base_document - 0.25),
                    min(0.85, base_graph + 0.25),
                    "llm:relationship",
                )
            if intent == "temporal":
                return (
                    max(0.15, base_document - 0.15),
                    min(0.85, base_graph + 0.15),
                    "llm:temporal",
                )
            if intent == "factual":
                return (
                    min(0.9, base_document + 0.2),
                    max(0.1, base_graph - 0.2),
                    "llm:factual",
                )
            if intent == "preference":
                return (
                    min(0.9, base_document + 0.1),
                    max(0.1, base_graph - 0.1),
                    "llm:preference",
                )

        # 回退：硬编码关键词匹配
        normalized = query.casefold()
        relation_terms = RELATION_TERMS
        temporal_terms = TEMPORAL_TERMS
        factual_terms = FACTUAL_TERMS

        relation_hit = any(term in normalized for term in relation_terms)
        temporal_hit = any(term in normalized for term in temporal_terms)
        factual_hit = any(term in normalized for term in factual_terms)

        document_weight = base_document
        graph_weight = base_graph

        if relation_hit:
            graph_weight += 0.2
            document_weight -= 0.2
            intent = "relationship"
        if temporal_hit:
            graph_weight += 0.1
            document_weight -= 0.1
            intent = "temporal" if intent == "default" else f"{intent}+temporal"
        if factual_hit and not relation_hit:
            document_weight += 0.15
            graph_weight -= 0.15
            intent = "factual" if intent == "default" else f"{intent}+factual"

        document_weight = max(0.15, min(0.9, document_weight))
        graph_weight = max(0.1, min(0.85, graph_weight))
        total = document_weight + graph_weight
        if total <= 0:
            return base_document, base_graph, "fixed"
        return document_weight / total, graph_weight / total, intent

    @staticmethod
    def _compute_strategy_weights(strategy: RecallStrategy) -> tuple[float, float]:
        weights = {
            RecallStrategy.CONTEXTUAL_SIMILARITY: (0.70, 0.30),
            RecallStrategy.TOPIC_ASSOCIATION: (0.65, 0.35),
            RecallStrategy.PREFERENCE_QUERY: (0.80, 0.20),
            RecallStrategy.RELATIONSHIP_REVIEW: (0.30, 0.70),
        }
        return weights.get(strategy, (0.50, 0.50))


__all__ = ["DualRouteRetriever"]
