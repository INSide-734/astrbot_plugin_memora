"""将文档检索路由与图检索路由融合为单一结果列表。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ..adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    ScoreDirection,
    ScoreSemantics,
    declared_adapter_contract,
)
from ..models.memory_evolution import ExpansionBudget, ScopeContext
from ..models.recall_strategy import RecallStrategy
from ..models.temporal import normalize_datetime
from .evidence_scorer import RetrievalEvidenceScorer
from .graph_retriever import GraphRetriever
from .hybrid_retriever import HybridRetriever
from .intent_keywords import FACTUAL_TERMS, RELATION_TERMS, TEMPORAL_TERMS
from .multi_query_fusion import fuse_query_results, split_candidate_budget
from .projection_reader import ProjectionBudget, ProjectionScope
from .provider_privacy_prefilter import (
    ProviderPrivacyContext,
    ProviderPrivacyPrefilter,
    rerank_with_provider_boundary,
)
from .retrieval_execution import RouteExecutionCoordinator
from .route_policy import should_use_graph_route
from .rrf_fusion import HybridResult

if TYPE_CHECKING:
    from .query_planner import QueryPlan
    from .query_rewriter import QueryIntent


class DualRouteRetriever:
    """协调文档检索路由与图检索路由。"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.HYBRID_RETRIEVER,
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.SCORING,
                AdapterCapability.CANCELLATION,
                AdapterCapability.REFERENCE_TIME,
            }
        ),
        score=ScoreSemantics(direction=ScoreDirection.HIGHER_IS_BETTER),
    )

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
        atom_retriever=None,
        evidence_scorer: RetrievalEvidenceScorer | None = None,
        create_tracked_task_cb: Callable[[Awaitable[Any]], None] | None = None,
    ):
        """装配文档、图与可选 Atom 证据路及后续排序组件。"""

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
        # v2.5: 可插拔重排序器（MMR / Embedding 相似度 / LLM / Hybrid）
        self.reranker = reranker
        self.derived_expander = derived_expander
        self.projection_reader = projection_reader
        self.atom_retriever = atom_retriever
        self.evidence_scorer = evidence_scorer
        self._create_tracked_task = create_tracked_task_cb
        self._route_coordinator = RouteExecutionCoordinator(
            document_retriever=document_retriever or self.document_retriever,
            graph_retriever=graph_retriever or self.graph_retriever,
            atom_retriever=atom_retriever,
        )
        self._reranker_strategy = self.config.get("reranker.strategy", "mmr")
        self._provider_prefilter = ProviderPrivacyPrefilter()
        # 阶段计时存储（每次 search() 后更新）

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
        query_plan: QueryPlan | None = None,
        timing_sink: dict[str, float | int | bool] | None = None,
        deadline_monotonic: float | None = None,
    ) -> list[HybridResult]:
        """同时运行两条检索路由，并合并候选记忆。

        参数:
            chat_type: 私聊或群聊场景，用于隐私过滤。
            query_intent: 查询改写结果，优先使用 LLM 意图做权重调整。
            user_id: 用户 ID，用于个性化排序。
            query_plan: 可选多查询计划；存在时按计划拆分预算并跨查询 RRF 融合。
        """
        if query_plan is not None and query_plan.queries:
            use_graph_route = should_use_graph_route(query_plan, query_intent)
            return await self._search_with_plan(
                query_plan=query_plan,
                k=k,
                session_id=session_id,
                persona_id=persona_id,
                strategy=strategy,
                memory_types=memory_types,
                chat_type=chat_type,
                query_intent=query_intent,
                user_id=user_id,
                reference_time=reference_time,
                timing_sink=timing_sink,
                deadline_monotonic=deadline_monotonic,
                use_graph_route=use_graph_route,
            )

        # 向后兼容：单查询路径
        reference_time = normalize_datetime(reference_time) or datetime.now(
            timezone.utc
        )
        outcome = await self._route_coordinator.execute(
            query=query,
            k=max(k * 2, k),
            session_id=session_id,
            persona_id=persona_id,
            memory_types=memory_types,
            reference_time=reference_time,
            deadline_monotonic=deadline_monotonic,
            use_graph_route=should_use_graph_route(query_plan, query_intent),
        )
        doc_results = outcome.document_results
        graph_results = outcome.graph_results
        atom_results = outcome.atom_results
        atom_scores, atom_ids_by_parent = self._aggregate_atom_evidence(atom_results)
        document_route_ms = outcome.timing.get("document_total_ms", 0.0)
        graph_route_ms = outcome.timing.get("graph_total_ms", 0.0)

        _t_merge_start = time.perf_counter()
        if not graph_results and not atom_scores:
            merged = list(doc_results)
        else:
            merged = await self._merge_dual_results(
                doc_results,
                graph_results,
                query,
                strategy=strategy,
                query_intent=query_intent,
                atom_scores=atom_scores,
            )
        merge_ms = (time.perf_counter() - _t_merge_start) * 1000.0

        # 人格感知记忆解读 — 当前 persona 匹配的记忆获得加权
        if persona_id and merged:
            merged = self._apply_persona_boost(merged, persona_id)

        # v2.5 个性化排序 — 基于用户画像标签加权
        _t_profile_start = time.perf_counter()
        if user_id and self.personalized_ranker and self.profile_manager and merged:
            try:
                tag_weights = await self.profile_manager.get_tag_weights(user_id)
                profile = await self.profile_manager.get_profile(user_id)
                merged = self.personalized_ranker.apply(merged, tag_weights, profile)
            except Exception:
                pass  # 个性化排序失败不影响主流程
        profile_lookup_ms = (time.perf_counter() - _t_profile_start) * 1000.0

        # v3.0 证据评分 — temporal/entity/focus/cross-query 维度打分
        if self.evidence_scorer is not None and query_plan is not None and merged:
            merged = self.evidence_scorer.score(merged, query_plan)

        _t_relation_start = time.perf_counter()
        evolution_config = self.config.get("memory_evolution", {})
        if not isinstance(evolution_config, dict):
            evolution_config = {}
        if (
            self.derived_expander is not None
            and _supports_declared_capability(
                self.derived_expander,
                AdapterCapability.REFERENCE_TIME,
            )
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
        relation_ms = (time.perf_counter() - _t_relation_start) * 1000.0

        _t_projection_start = time.perf_counter()
        if (
            self.projection_reader is not None
            and _supports_declared_capability(
                self.projection_reader,
                AdapterCapability.REFERENCE_TIME,
            )
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
        projection_ms = (time.perf_counter() - _t_projection_start) * 1000.0

        # v2.5 可插拔重排序 — MMR / Embedding 相似度 / LLM / Hybrid
        _t_rerank_start = time.perf_counter()
        if self.reranker and len(merged) > 1:
            merged = await self._apply_reranker(
                merged,
                k,
                query=query,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
                user_id=user_id,
            )
        else:
            merged.sort(key=lambda item: item.final_score, reverse=True)
        rerank_ms = (time.perf_counter() - _t_rerank_start) * 1000.0

        # 存储阶段计时
        self.last_search_timing = {
            **outcome.timing,
            "document_route_ms": document_route_ms,
            "graph_route_ms": graph_route_ms,
            "merge_ms": merge_ms,
            "relation_ms": relation_ms,
            "projection_ms": projection_ms,
            "profile_lookup_ms": profile_lookup_ms,
            "derived_expansion_ms": relation_ms + projection_ms,
            "rerank_ms": rerank_ms,
            "query_count": 1,
        }

        _t_privacy_start = time.perf_counter()
        visible = self._filter_by_privacy(merged, chat_type)[:k]
        privacy_ms = (time.perf_counter() - _t_privacy_start) * 1000.0
        self.last_search_timing["privacy_ms"] = privacy_ms
        if timing_sink is not None:
            timing_sink.update(self.last_search_timing)
        await self._schedule_atom_touch(visible, atom_ids_by_parent)
        return visible

    async def _search_with_plan(
        self,
        *,
        query_plan: QueryPlan,
        k: int,
        session_id: str | None,
        persona_id: str | None,
        strategy: RecallStrategy | None,
        memory_types: list[str] | None,
        chat_type: str,
        query_intent: QueryIntent | None,
        user_id: str | None,
        reference_time: datetime | None,
        timing_sink: dict[str, float | int | bool] | None,
        deadline_monotonic: float | None,
        use_graph_route: bool,
    ) -> list[HybridResult]:
        """多查询计划路径：按计划拆分预算，逐查询检索并跨查询 RRF 融合。"""
        reference_time = normalize_datetime(reference_time) or datetime.now(
            timezone.utc
        )

        queries = list(query_plan.queries)
        budgets = split_candidate_budget(max(k * 2, k), len(queries))

        per_query_merged: list[list[HybridResult]] = []
        all_atom_ids_by_parent: dict[int, list[int]] = {}
        document_route_total_ms = 0.0
        graph_route_total_ms = 0.0
        merge_total_ms = 0.0
        route_timing: dict[str, float | bool] = {}

        active_queries = [
            (sub_query, budget)
            for sub_query, budget in zip(queries, budgets)
            if budget > 0
        ]
        outcomes = await asyncio.gather(
            *(
                self._route_coordinator.execute(
                    query=sub_query,
                    k=budget,
                    session_id=session_id,
                    persona_id=persona_id,
                    memory_types=memory_types,
                    reference_time=reference_time,
                    deadline_monotonic=deadline_monotonic,
                    use_graph_route=use_graph_route,
                )
                for sub_query, budget in active_queries
            )
        )

        for (sub_query, budget), outcome in zip(active_queries, outcomes):
            document_route_total_ms = max(
                document_route_total_ms,
                outcome.timing.get("document_total_ms", 0.0),
            )
            graph_route_total_ms = max(
                graph_route_total_ms,
                outcome.timing.get("graph_total_ms", 0.0),
            )
            for key, value in outcome.timing.items():
                if isinstance(value, bool):
                    route_timing[key] = value
                elif isinstance(value, (int, float)):
                    route_timing[key] = max(route_timing.get(key, 0.0), float(value))

            atom_scores, atom_ids_map = self._aggregate_atom_evidence(
                outcome.atom_results
            )
            for parent_id, atom_id_list in atom_ids_map.items():
                all_atom_ids_by_parent.setdefault(parent_id, []).extend(atom_id_list)

            _t_merge_start = time.perf_counter()
            if not outcome.graph_results and not atom_scores:
                merged = list(outcome.document_results)
            else:
                merged = await self._merge_dual_results(
                    outcome.document_results,
                    outcome.graph_results,
                    sub_query,
                    strategy=strategy,
                    query_intent=query_intent,
                    atom_scores=atom_scores,
                )
            merge_total_ms += (time.perf_counter() - _t_merge_start) * 1000.0

            merged.sort(key=lambda item: (-item.final_score, item.doc_id))
            per_query_merged.append(merged[:budget])

        # 跨查询 RRF 融合
        fused = fuse_query_results(
            per_query_merged,
            sum(len(results) for results in per_query_merged),
        )

        # 人格感知记忆解读
        if persona_id and fused:
            fused = self._apply_persona_boost(fused, persona_id)

        # 个性化排序
        _t_profile_start = time.perf_counter()
        if user_id and self.personalized_ranker and self.profile_manager and fused:
            try:
                tag_weights = await self.profile_manager.get_tag_weights(user_id)
                profile = await self.profile_manager.get_profile(user_id)
                fused = self.personalized_ranker.apply(fused, tag_weights, profile)
            except Exception:
                pass
        profile_lookup_ms = (time.perf_counter() - _t_profile_start) * 1000.0

        # v3.0 证据评分 — temporal/entity/focus/cross-query 维度打分
        if self.evidence_scorer is not None and fused:
            fused = self.evidence_scorer.score(fused, query_plan)

        # 派生扩展与投射（仅当配置启用时）
        _t_relation_start = time.perf_counter()
        evolution_config = self.config.get("memory_evolution", {})
        if not isinstance(evolution_config, dict):
            evolution_config = {}
        if (
            self.derived_expander is not None
            and _supports_declared_capability(
                self.derived_expander,
                AdapterCapability.REFERENCE_TIME,
            )
            and bool(evolution_config.get("enabled", False))
            and str(evolution_config.get("mode", "disabled")) in {"readonly", "active"}
            and fused
        ):
            baseline = list(fused)
            try:
                max_expansions = max(
                    0,
                    int(evolution_config.get("max_query_expansions", 8)),
                )
                fused = await self.derived_expander.expand(
                    fused,
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
                        max_items=len(fused) + max_expansions,
                    ),
                    reference_time=reference_time,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                fused = baseline
        relation_ms = (time.perf_counter() - _t_relation_start) * 1000.0

        _t_projection_start = time.perf_counter()
        if (
            self.projection_reader is not None
            and _supports_declared_capability(
                self.projection_reader,
                AdapterCapability.REFERENCE_TIME,
            )
            and bool(evolution_config.get("enabled", False))
            and str(evolution_config.get("mode", "disabled")) in {"readonly", "active"}
            and fused
        ):
            baseline = list(fused)
            try:
                projection_budget_chars = max(
                    0,
                    int(evolution_config.get("projection_budget_chars", 2_000)),
                )
                projection_limit = max(
                    0,
                    int(evolution_config.get("max_query_expansions", 8)),
                )
                fused = await self.projection_reader.attach(
                    fused,
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
                fused = baseline
        projection_ms = (time.perf_counter() - _t_projection_start) * 1000.0

        # 重排序
        _t_rerank_start = time.perf_counter()
        if self.reranker and len(fused) > 1:
            fused = await self._apply_reranker(
                fused,
                k,
                query=query_plan.original_query,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
                user_id=user_id,
            )
        else:
            fused.sort(key=lambda item: item.final_score, reverse=True)
        rerank_ms = (time.perf_counter() - _t_rerank_start) * 1000.0

        self.last_search_timing = {
            **route_timing,
            "document_route_ms": document_route_total_ms,
            "graph_route_ms": graph_route_total_ms,
            "merge_ms": merge_total_ms,
            "relation_ms": relation_ms,
            "projection_ms": projection_ms,
            "profile_lookup_ms": profile_lookup_ms,
            "derived_expansion_ms": relation_ms + projection_ms,
            "rerank_ms": rerank_ms,
            "query_count": len(active_queries),
        }

        _t_privacy_start = time.perf_counter()
        visible = self._filter_by_privacy(fused, chat_type)[:k]
        privacy_ms = (time.perf_counter() - _t_privacy_start) * 1000.0
        self.last_search_timing["privacy_ms"] = privacy_ms
        if timing_sink is not None:
            timing_sink.update(self.last_search_timing)
        await self._schedule_atom_touch(visible, all_atom_ids_by_parent)
        return visible

    async def _search_atom_evidence(
        self,
        query: str,
        *,
        k: int,
        session_id: str | None,
        persona_id: str | None,
    ) -> list[Any]:
        """查询内部 Atom 证据；普通故障返回空信号，取消异常向上传播。"""

        if self.atom_retriever is None:
            return []
        try:
            return await self.atom_retriever.search(
                query,
                k=k,
                session_id=session_id,
                persona_id=persona_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[DualRouteRetriever] Atom 证据路降级，异常类型=%s",
                exc.__class__.__name__,
            )
            return []

    @staticmethod
    def _aggregate_atom_evidence(
        atom_results: list[Any],
    ) -> tuple[dict[int, float], dict[int, list[int]]]:
        """按父 canonical 聚合 Atom 分数，并把内部 ID 留在请求局部。"""

        scores_by_parent: dict[int, list[float]] = {}
        ids_by_parent: dict[int, list[int]] = {}
        for result in atom_results:
            parent_id = int(getattr(result, "parent_memory_id", 0) or 0)
            atom_id = int(getattr(result, "atom_id", 0) or 0)
            if parent_id <= 0 or atom_id <= 0:
                continue
            score = max(0.0, min(1.0, float(getattr(result, "final_score", 0.0))))
            scores_by_parent.setdefault(parent_id, []).append(score)
            ids_by_parent.setdefault(parent_id, []).append(atom_id)

        aggregated: dict[int, float] = {}
        for parent_id, scores in scores_by_parent.items():
            ranked = sorted(scores, reverse=True)
            secondary_bonus = min(0.15, sum(ranked[1:]) * 0.05)
            aggregated[parent_id] = min(1.0, ranked[0] + secondary_bonus)
        return aggregated, ids_by_parent

    async def _touch_atom_evidence(
        self,
        results: list[HybridResult],
        ids_by_parent: dict[int, list[int]],
    ) -> None:
        """只更新最终可见 canonical 结果实际贡献的内部 Atom。"""

        if self.atom_retriever is None or not ids_by_parent:
            return
        atom_ids = sorted(
            {
                atom_id
                for result in results
                for atom_id in ids_by_parent.get(result.doc_id, [])
            }
        )
        if not atom_ids:
            return
        try:
            await self.atom_retriever.touch_many(atom_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[DualRouteRetriever] Atom 访问反馈失败，异常类型=%s",
                exc.__class__.__name__,
            )

    async def _schedule_atom_touch(
        self,
        results: list[HybridResult],
        ids_by_parent: dict[int, list[int]],
    ) -> None:
        """把非关键 Atom 访问反馈交给引擎生命周期任务管理器。"""

        touch = self._touch_atom_evidence(results, ids_by_parent)
        if self._create_tracked_task is None:
            await touch
            return
        self._create_tracked_task(touch)

    async def _apply_reranker(
        self,
        results: list[HybridResult],
        k: int,
        *,
        query: str,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        user_id: str | None,
    ) -> list[HybridResult]:
        """携带当前请求授权上下文执行同步或异步重排。"""

        return await rerank_with_provider_boundary(
            results,
            k,
            query=query,
            reranker=self.reranker,
            strategy=str(self._reranker_strategy),
            prefilter=self._provider_prefilter,
            context=ProviderPrivacyContext(
                chat_type=chat_type,
                scope_key=session_id or persona_id or f"{chat_type}:default",
                stable_user_id=user_id,
            ),
            strict_mode=bool(self.config.get("security.strict_mode", False)),
            mmr_lambda=float(self.config.get("reranker.mmr_lambda", 0.7)),
        )

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
        atom_scores: dict[int, float] | None = None,
    ) -> list[HybridResult]:
        """把文档、图与 Atom 父级证据统一融合为 canonical 结果。"""

        atom_scores = atom_scores or {}
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
        atom_max = max(atom_scores.values(), default=1.0) or 1.0
        atom_weight = (
            max(0.0, min(0.5, float(self.config.get("atom_route_weight", 0.25))))
            if atom_scores
            else 0.0
        )
        base_route_scale = 1.0 - atom_weight
        document_weight *= base_route_scale
        graph_weight *= base_route_scale

        doc_map = {item.doc_id: item for item in doc_results}
        graph_map = {item.doc_id: item for item in graph_results}
        all_doc_ids = set(doc_map) | set(graph_map) | set(atom_scores)

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
            atom_signal = atom_scores.get(doc_id, 0.0) / atom_max
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
                + atom_weight * atom_signal
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

        merged_results.sort(key=lambda item: (-item.final_score, item.doc_id))
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


def _supports_declared_capability(adapter: Any, capability: AdapterCapability) -> bool:
    """显式 contract 优先；缺失 contract 的历史替身保留旧行为。"""

    contract = declared_adapter_contract(adapter)
    return contract is None or contract.supports(capability)
