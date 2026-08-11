"""
混合检索器 - 结合BM25和向量检索的混合检索
实现并行检索、RRF融合和智能加权策略
"""

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ..adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    ScoreDirection,
    ScoreSemantics,
)
from ..shared.mmr import apply_mmr
from .bm25_retriever import BM25Retriever
from .memory_lifecycle import MemoryLifecycleManager
from .rrf_fusion import BM25Result, HybridResult, RRFFusion, VectorResult
from .score_weighting import ScoreWeighting
from .vector_deadline import run_local_and_bounded_vector
from .vector_retriever import VectorRetriever


class HybridRetriever:
    """
    混合检索器

    结合BM25稀疏检索和向量密集检索,通过RRF融合结果,
    并应用重要性和时间衰减加权策略。

    主要特性:
    1. 并行执行BM25和向量检索(使用asyncio.gather)
    2. 使用RRF算法融合两路结果
    3. 应用重要性加权和时间衰减
    4. 支持退化机制(某一路失败时使用另一路)
    5. 确保两个索引中doc_id的一致性
    """

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.HYBRID_RETRIEVER,
        native=frozenset({AdapterCapability.SCORING}),
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.UPDATE,
                AdapterCapability.DELETE,
                AdapterCapability.CANCELLATION,
                AdapterCapability.REFERENCE_TIME,
            }
        ),
        score=ScoreSemantics(direction=ScoreDirection.HIGHER_IS_BETTER),
    )

    def __init__(
        self,
        bm25_retriever: BM25Retriever,
        vector_retriever: VectorRetriever,
        rrf_fusion: RRFFusion,
        config: dict[str, Any] | None = None,
    ):
        """
        初始化混合检索器

        Args:
            bm25_retriever: BM25检索器实例
            vector_retriever: 向量检索器实例
            rrf_fusion: RRF融合器实例
            config: 配置字典,支持以下参数:
                - decay_rate: 时间衰减率,默认0.01
                - importance_weight: 重要性权重,默认1.0
                - fallback_enabled: 启用退化机制,默认True
        """
        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever
        self.rrf_fusion = rrf_fusion
        self.config = config or {}

        # 配置参数
        self.decay_rate = self.config.get("decay_rate", 0.01)
        self.importance_weight = self.config.get("importance_weight", 1.0)
        self.fallback_enabled = self.config.get("fallback_enabled", True)

        # 加权求和各维度权重（可通过配置覆盖）
        self.score_alpha = self.config.get(
            "hybrid_scoring.score_alpha", 0.5
        )  # 检索相关性
        self.score_beta = self.config.get("hybrid_scoring.score_beta", 0.25)  # 重要性
        self.score_gamma = self.config.get(
            "hybrid_scoring.score_gamma", 0.25
        )  # 时间新鲜度

        # MMR 多样性参数
        self.mmr_lambda = self.config.get(
            "hybrid_scoring.mmr_lambda", 0.7
        )  # 相关性 vs 多样性权衡

        # 内部子模块
        self.memory_lifecycle = MemoryLifecycleManager(bm25_retriever, vector_retriever)
        self.score_weighting = ScoreWeighting(
            decay_rate=self.decay_rate,
            importance_weight=self.importance_weight,
            score_alpha=self.score_alpha,
            score_beta=self.score_beta,
            score_gamma=self.score_gamma,
            recency_bump_enabled=bool(
                self.config.get("human_like_memory.recency_bump_enabled", True)
            ),
        )

    @staticmethod
    async def _search_route(
        route_name: str, search_coro
    ) -> tuple[list, Exception | None, float]:
        """执行单条检索路由，并把普通失败转换为可降级的路由错误。"""
        started = time.perf_counter()
        try:
            return await search_coro, None, (time.perf_counter() - started) * 1000.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[混合检索] 路由失败，路由=%s，异常类型=%s",
                route_name,
                exc.__class__.__name__,
            )
            return [], exc, (time.perf_counter() - started) * 1000.0

    async def add_memory(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> int:
        """添加记忆到两个索引（委托给 MemoryLifecycleManager）"""
        return await self.memory_lifecycle.add_memory(content, metadata)

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        memory_types: list[str] | None = None,
        timing_sink: dict[str, object] | None = None,
        **kwargs: Any,
    ) -> list[HybridResult]:
        """
        执行混合检索

        Args:
            query: 查询字符串
            k: 返回的结果数量
            session_id: 会话ID过滤(可选)
            persona_id: 人格ID过滤(可选)

        Returns:
            List[HybridResult]: 混合检索结果,按最终分数降序排列
        """
        if not query or not query.strip():
            if timing_sink is not None:
                timing_sink.update(
                    {
                        "bm25_ms": 0.0,
                        "vector_ms": 0.0,
                        "document_fusion_ms": 0.0,
                        "document_weighting_ms": 0.0,
                        "document_mmr_ms": 0.0,
                        "document_total_ms": 0.0,
                    }
                )
            return []

        # 1. 并行执行两路检索
        _t_route_start = time.perf_counter()
        deadline_monotonic = kwargs.get("deadline_monotonic")
        (
            local_route,
            vector_route,
            vector_timed_out,
        ) = await run_local_and_bounded_vector(
            lambda: self._search_route(
                "BM25",
                self.bm25_retriever.search(query, k, session_id, persona_id),
            ),
            lambda: self._search_route(
                "向量",
                self.vector_retriever.search(query, k, session_id, persona_id),
            ),
            deadline_monotonic=(
                float(deadline_monotonic)
                if isinstance(deadline_monotonic, (int, float))
                and not isinstance(deadline_monotonic, bool)
                else None
            ),
        )
        bm25_results, bm25_error, bm25_ms = local_route
        if vector_route is None:
            vector_results, vector_error, vector_ms = [], TimeoutError(), 0.0
        else:
            vector_results, vector_error, vector_ms = vector_route
        if timing_sink is not None:
            timing_sink["bm25_ms"] = bm25_ms
            timing_sink["vector_ms"] = vector_ms
            if vector_timed_out:
                timing_sink.update(
                    {
                        "document_vector_timed_out": True,
                        "deadline_exhausted": True,
                        "partial_fallback": bool(bm25_results),
                    }
                )

        # 2. 处理退化情况
        if bm25_error and vector_error:
            if timing_sink is not None:
                timing_sink.update(
                    {
                        "document_route_degraded": True,
                        "document_fusion_ms": 0.0,
                        "document_weighting_ms": 0.0,
                        "document_mmr_ms": 0.0,
                        "document_total_ms": (time.perf_counter() - _t_route_start)
                        * 1000.0,
                    }
                )
            return []

        if bm25_error:
            if self.fallback_enabled and vector_results:
                weighted_started = time.perf_counter()
                fallback = self._fallback_vector_only(vector_results, k)
                if timing_sink is not None:
                    timing_sink.update(
                        {
                            "document_fusion_ms": 0.0,
                            "document_weighting_ms": (
                                time.perf_counter() - weighted_started
                            )
                            * 1000.0,
                            "document_mmr_ms": 0.0,
                            "document_total_ms": (time.perf_counter() - _t_route_start)
                            * 1000.0,
                        }
                    )
                return fallback
            if timing_sink is not None:
                timing_sink.update(
                    {
                        "document_fusion_ms": 0.0,
                        "document_weighting_ms": 0.0,
                        "document_mmr_ms": 0.0,
                        "document_total_ms": (time.perf_counter() - _t_route_start)
                        * 1000.0,
                    }
                )
            return []

        if vector_error:
            if self.fallback_enabled and bm25_results:
                weighted_started = time.perf_counter()
                fallback = self._fallback_bm25_only(bm25_results, k)
                if timing_sink is not None:
                    timing_sink.update(
                        {
                            "document_fusion_ms": 0.0,
                            "document_weighting_ms": (
                                time.perf_counter() - weighted_started
                            )
                            * 1000.0,
                            "document_mmr_ms": 0.0,
                            "document_total_ms": (time.perf_counter() - _t_route_start)
                            * 1000.0,
                        }
                    )
                return fallback
            if timing_sink is not None:
                timing_sink.update(
                    {
                        "document_fusion_ms": 0.0,
                        "document_weighting_ms": 0.0,
                        "document_mmr_ms": 0.0,
                        "document_total_ms": (time.perf_counter() - _t_route_start)
                        * 1000.0,
                    }
                )
            return []

        # 3. RRF融合
        # 转换结果类型以匹配RRF融合器期望的类型
        rrf_bm25_results = [
            BM25Result(
                doc_id=r.doc_id, score=r.score, content=r.content, metadata=r.metadata
            )
            for r in bm25_results
        ]

        rrf_vector_results = [
            VectorResult(
                doc_id=r.doc_id, score=r.score, content=r.content, metadata=r.metadata
            )
            for r in vector_results
        ]

        _t_fusion_start = time.perf_counter()
        fused_results = self.rrf_fusion.fuse(
            rrf_bm25_results, rrf_vector_results, top_k=k
        )
        _t_fusion_end = time.perf_counter()
        if timing_sink is not None:
            timing_sink["document_fusion_ms"] = (
                _t_fusion_end - _t_fusion_start
            ) * 1000.0

        if not fused_results:
            if timing_sink is not None:
                timing_sink.update(
                    {
                        "document_weighting_ms": 0.0,
                        "document_mmr_ms": 0.0,
                        "document_total_ms": (time.perf_counter() - _t_route_start)
                        * 1000.0,
                    }
                )
            return []

        # 4. 应用加权（通过线程池卸载 CPU 密集型 json.loads + 循环）
        current_time = time.time()
        _t_weight_start = time.perf_counter()
        weighted_results = await asyncio.to_thread(
            self.score_weighting.apply_weighting, fused_results, current_time
        )
        _t_weight_end = time.perf_counter()
        if timing_sink is not None:
            timing_sink["document_weighting_ms"] = (
                _t_weight_end - _t_weight_start
            ) * 1000.0

        # 5. MMR 去重（通过线程池卸载 O(k*n) Jaccard 集合运算）
        _t_mmr_start = time.perf_counter()
        if len(weighted_results) > 1:
            weighted_results = await asyncio.to_thread(
                apply_mmr, weighted_results, k, self.mmr_lambda
            )
        _t_mmr_end = time.perf_counter()
        if timing_sink is not None:
            timing_sink["document_mmr_ms"] = (_t_mmr_end - _t_mmr_start) * 1000.0

        # 6. 记忆类型后处理过滤
        if memory_types:
            memory_types_lower = {mt.lower() for mt in memory_types}
            for result in weighted_results:
                atom_type = result.metadata.get("memory_type") or result.metadata.get(
                    "atom_type", ""
                )
                if atom_type.lower() not in memory_types_lower:
                    result.final_score *= 0.1
            weighted_results.sort(key=lambda r: r.final_score, reverse=True)

        if timing_sink is not None:
            timing_sink["document_total_ms"] = (
                time.perf_counter() - _t_route_start
            ) * 1000.0

        return weighted_results

    def _fallback_bm25_only(self, bm25_results: list, k: int) -> list[HybridResult]:
        """
        BM25退化:仅使用BM25结果

        Args:
            bm25_results: BM25检索结果
            k: 返回的结果数量

        Returns:
            List[HybridResult]: 退化后的结果
        """
        # 将BM25结果转换为FusedResult
        fused_results = self.rrf_fusion._convert_bm25_only(bm25_results, k)

        # 应用加权
        current_time = time.time()
        return self.score_weighting.apply_weighting(fused_results, current_time)

    def _fallback_vector_only(self, vector_results: list, k: int) -> list[HybridResult]:
        """
        向量退化:仅使用向量结果

        Args:
            vector_results: 向量检索结果
            k: 返回的结果数量

        Returns:
            List[HybridResult]: 退化后的结果
        """
        # 将向量结果转换为FusedResult
        fused_results = self.rrf_fusion._convert_vector_only(vector_results, k)

        # 应用加权
        current_time = time.time()
        return self.score_weighting.apply_weighting(fused_results, current_time)

    async def update_metadata(
        self,
        doc_id: int,
        metadata: dict[str, Any],
        expected_revision: str | None = None,
        advance_revision: bool = True,
    ) -> bool:
        """同步更新元数据并可执行 revision CAS；维护字段可保留原 revision。"""
        update_kwargs: dict[str, Any] = {}
        if not advance_revision:
            update_kwargs["advance_revision"] = False
        if expected_revision is None:
            return await self.memory_lifecycle.update_metadata(
                doc_id,
                metadata,
                **update_kwargs,
            )
        return await self.memory_lifecycle.update_metadata(
            doc_id,
            metadata,
            expected_revision=expected_revision,
            **update_kwargs,
        )

    async def update_content_if_revision(
        self,
        doc_id: int,
        content: str,
        metadata: dict[str, Any],
        expected_revision: str,
    ) -> bool:
        """同步执行带 revision CAS 的 canonical 正文更新。"""

        return await self.memory_lifecycle.update_content_if_revision(
            doc_id,
            content,
            metadata,
            expected_revision,
        )

    async def delete_memory(self, doc_id: int) -> bool:
        """从多个存储层中删除记忆（委托给 MemoryLifecycleManager）"""
        return await self.memory_lifecycle.delete_memory(doc_id)
