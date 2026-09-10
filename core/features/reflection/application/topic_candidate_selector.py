"""话题候选选择器：编排 BM25 主排序、近期/高频补足和安全降级。

简化版本：
- 四种模式：off/observe/full/top_k
- 直接调用 TopicCatalogStore 查询端口
- 所有失败降级为 baseline 空候选
- 不阻塞 canonical 主链
"""

from __future__ import annotations

import asyncio
import time
import unicodedata

from astrbot.api import logger

from ...memory.infrastructure.topic_catalog_store import TopicCatalogStore
from ...recall.processors.conversation_formatter import (
    ConversationFormatter,
)
from ...recall.processors.text_processor import TextProcessor
from ..domain.config import CandidateReuseConfig
from ..domain.summary_models import (
    SourceWindow,
    TopicCandidateContext,
    TopicCandidateMode,
    TopicCandidateSelection,
)
from .topic_query_builder import (
    build_topic_fts_query,
)


class TopicCandidateSelector:
    """编排 BM25 主排序、近期/高频补足和双预算门控。"""

    def __init__(
        self,
        catalog_store: TopicCatalogStore,
        text_processor: TextProcessor,
        conversation_formatter: ConversationFormatter,
    ) -> None:
        """初始化候选选择器。

        Args:
            catalog_store: topic 目录查询端口
            text_processor: 文本处理器（tokenization）
            conversation_formatter: 消息格式化器
        """
        self._catalog = catalog_store
        self._text_processor = text_processor
        self._formatter = conversation_formatter

    async def select_candidates(
        self,
        window: SourceWindow,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
    ) -> TopicCandidateSelection:
        """选择话题候选并返回不可变结果。

        Args:
            window: 固定来源窗口
            context: 已确认的 scope/privacy/chat type/resolver revision 快照
            config: 不可变配置快照

        Returns:
            TopicCandidateSelection: 包含 labels、mode、统计和 metrics
        """
        start_time = time.perf_counter()

        try:
            catalog_status, generation = await self._get_catalog_state()
            effective_config = await self._get_effective_config(
                context, config, generation
            )
            effective_mode = effective_config[0]
            effective_k = effective_config[1]

            # 快速路径：off 或 scope 不可用
            if effective_mode == "off" or context.scope_reason_code != "scope_resolved":
                return self._baseline_result(
                    mode=effective_mode,
                    reason=context.scope_reason_code
                    if context.scope_reason_code != "scope_resolved"
                    else "mode_off",
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                )

            # scope_resolved 守卫已保证隐私/会话类型非 None；
            # 显式窄化供类型检查，缺失即 fail-closed 早退（上方守卫）。
            assert context.privacy_level is not None
            assert context.chat_type is not None

            # catalog degraded、generation 缺失或未就绪时不得启用候选。
            if catalog_status != "ready" or generation is None:
                logger.warning(
                    f"话题目录状态为 {catalog_status}，强制降级到 observe 模式"
                )
                return self._baseline_result(
                    mode="observe",
                    reason=f"catalog_{catalog_status}",
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                )

            runtime_config = CandidateReuseConfig(
                mode=TopicCandidateMode(effective_mode).value,
                fixed_k=effective_k or config.fixed_k,
                activation_threshold=config.activation_threshold,
                max_full_topics=config.max_full_topics,
                max_full_prompt_tokens=config.max_full_prompt_tokens,
                max_query_chars=config.max_query_chars,
                overfetch_factor=config.overfetch_factor,
                metrics_retention_days=config.metrics_retention_days,
                observe_max_candidates=config.observe_max_candidates,
                observe_max_rows=config.observe_max_rows,
                observe_max_duration_ms=config.observe_max_duration_ms,
                bucket_overrides=config.bucket_overrides,
            )

            if effective_mode == "observe":
                result = await self._select_observe_shadow(
                    window, context, runtime_config, generation, start_time
                )
            elif effective_mode == "full":
                result = await self._select_full_candidates(
                    window, context, runtime_config, generation, start_time
                )
            elif effective_mode == "top_k":
                result = await self._select_top_k_candidates(
                    window, context, runtime_config, generation, start_time
                )
            else:
                result = self._baseline_result(
                    mode="observe",
                    reason="unknown_mode",
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                )

            return result

        except asyncio.CancelledError:
            raise
        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.warning("话题候选选择失败，原因=selector_failed")
            return self._baseline_result(
                mode="observe" if config.mode != "off" else "off",
                reason="selector_failed",
                duration_ms=duration_ms,
            )

    async def _get_effective_config(
        self,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
        generation: int | None,
    ) -> tuple[str, int | None]:
        """根据 ready generation 的 scope 聚合 topic 规模获取有效配置。"""
        if (
            generation is None
            or context.privacy_level is None
            or context.chat_type is None
        ):
            return config.mode, config.fixed_k
        topic_count = await self._catalog.count_scope_topics(
            scope_key=context.scope_key,
            privacy_level=context.privacy_level,
            chat_type=context.chat_type,
            generation=generation,
        )
        bucket = self._map_topic_count_to_bucket(topic_count)
        return config.get_bucket_config(bucket)

    @staticmethod
    def _map_topic_count_to_bucket(count: int) -> str:
        """将 catalog 话题数量映射为固定规模桶。"""
        if count < 10:
            return "tiny"
        if count < 30:
            return "small"
        if count < 100:
            return "medium"
        if count < 300:
            return "large"
        if count < 1000:
            return "xlarge"
        return "huge"

    async def _get_catalog_state(self) -> tuple[str, int | None]:
        """读取状态和 active generation，供一次 selector 读取使用。"""
        try:
            db = self._catalog.db_connection
            if db is None:
                return "unknown", None
            cursor = await db.execute(
                "SELECT status, active_generation FROM topic_catalog_state WHERE id = 1"
            )
            row = await cursor.fetchone()
            if row is None:
                return "empty", None
            status = str(row[0])
            if status not in ("ready", "empty", "backfilling", "degraded"):
                return "unknown", None
            generation = row[1] if len(row) > 1 else 1
            if status != "ready" or not isinstance(generation, int) or generation <= 0:
                return status, None
            return status, generation
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("读取 catalog 状态失败", exc_info=True)
            return "unknown", None

    async def _get_catalog_status(self) -> str:
        """兼容旧调用方，仅返回当前 catalog 状态。"""
        status, _ = await self._get_catalog_state()
        return status

    async def _select_observe_shadow(
        self,
        window: SourceWindow,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
        generation: int,
        start_time: float,
    ) -> TopicCandidateSelection:
        """observe 影子计算：计算候选但不改变 Provider Prompt。

        bounded shadow 语义：observe_max_candidates 约束候选数上限，
        observe_max_rows 通过 SQL 行上限约束扫描量；超时上限由
        selector_duration_ms 观测暴露（异步 SQL 无法安全中断，不引入
        强制取消路径）。
        """
        shadow_config = CandidateReuseConfig(
            mode="full",
            activation_threshold=config.activation_threshold,
            fixed_k=config.fixed_k,
            max_full_topics=min(config.observe_max_candidates, config.observe_max_rows),
            max_full_prompt_tokens=config.max_full_prompt_tokens,
            max_query_chars=config.max_query_chars,
            overfetch_factor=config.overfetch_factor,
            metrics_retention_days=config.metrics_retention_days,
        )

        shadow_start = time.perf_counter()
        shadow_result = await self._select_full_candidates(
            window, context, shadow_config, generation, shadow_start, is_shadow=True
        )
        duration_ms = shadow_result.selector_duration_ms
        # 超时上限只在观测字段表达：bounded shadow 不改变 Provider Prompt，
        # 强制中断异步 SQL 反而会引入取消传播复杂度。
        if duration_ms > config.observe_max_duration_ms:
            shadow_result = TopicCandidateSelection(
                labels=shadow_result.labels,
                source_provenance_complete=False,
                mode=TopicCandidateMode.OBSERVE,
                effective_mode=TopicCandidateMode.OFF,
                catalog_status=shadow_result.catalog_status,
                topic_count_bucket=shadow_result.topic_count_bucket,
                candidate_count=shadow_result.candidate_count,
                bm25_hit_count=shadow_result.bm25_hit_count,
                recent_fill_count=shadow_result.recent_fill_count,
                identity_drop_count=shadow_result.identity_drop_count,
                budget_reason="observe_duration_exceeded",
                reason_code=f"observe_shadow_{shadow_result.reason_code}",
                selector_duration_ms=duration_ms,
            )

        # 强制 effective_mode 为 OFF
        return TopicCandidateSelection(
            labels=shadow_result.labels,
            source_provenance_complete=False,  # 强制 production_labels 为空
            mode=TopicCandidateMode.OBSERVE,
            effective_mode=TopicCandidateMode.OFF,
            catalog_status=shadow_result.catalog_status,
            topic_count_bucket=shadow_result.topic_count_bucket,
            candidate_count=shadow_result.candidate_count,
            bm25_hit_count=shadow_result.bm25_hit_count,
            recent_fill_count=shadow_result.recent_fill_count,
            identity_drop_count=shadow_result.identity_drop_count,
            budget_reason=shadow_result.budget_reason,
            reason_code=f"observe_shadow_{shadow_result.reason_code}",
            selector_duration_ms=shadow_result.selector_duration_ms,
        )

    async def _select_full_candidates(
        self,
        window: SourceWindow,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
        generation: int,
        start_time: float,
        is_shadow: bool = False,
    ) -> TopicCandidateSelection:
        """有界全量读取：从 scope_topics 聚合表读取全部候选。"""
        # scope_resolved 守卫（select_candidates）已保证非 None；
        # 子方法内重新断言供类型检查，None 时 AssertionError 走外层降级。
        assert context.privacy_level is not None
        assert context.chat_type is not None
        # 上限探测取 max+1：仅靠 SQL LIMIT 截断会让超限检查永不触发，
        # 把"超限返回空候选"退化成"静默返回截断的伪 full"。
        probe_count = config.max_full_topics + 1
        raw_candidates = await self._catalog.select_full_candidates(
            generation=generation,
            scope_key=context.scope_key,
            privacy_level=context.privacy_level,
            chat_type=context.chat_type,
            resolver_revision=context.resolver_revision,
            max_count=probe_count,
        )

        if not raw_candidates:
            return self._baseline_result(
                mode=config.mode,
                reason="no_full_candidates",
                catalog_status="ready",
                topic_count_bucket=self._bucket_topic_count(0),
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        labels = tuple(row["display_topic"] for row in raw_candidates)
        topic_count = len(labels)

        # 双上限检查
        if topic_count > config.max_full_topics:
            return self._baseline_result(
                mode=config.mode,
                reason="count_budget_exceeded",
                catalog_status="ready",
                topic_count_bucket=self._bucket_topic_count(topic_count),
                candidate_count=topic_count,
                budget_reason="count_exceeded",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        estimated_tokens = self._estimate_tokens(labels)
        if estimated_tokens > config.max_full_prompt_tokens:
            return self._baseline_result(
                mode=config.mode,
                reason="token_budget_exceeded",
                catalog_status="ready",
                topic_count_bucket=self._bucket_topic_count(topic_count),
                candidate_count=topic_count,
                budget_reason="token_exceeded",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        duration_ms = (time.perf_counter() - start_time) * 1000

        return TopicCandidateSelection(
            labels=labels,
            source_provenance_complete=True if not is_shadow else False,
            mode=config.mode,
            effective_mode=TopicCandidateMode.FULL
            if not is_shadow
            else TopicCandidateMode.OFF,
            catalog_status="ready",
            topic_count_bucket=self._bucket_topic_count(topic_count),
            candidate_count=len(labels),
            bm25_hit_count=0,
            recent_fill_count=0,
            identity_drop_count=0,
            budget_reason="",
            reason_code="full_success",
            selector_duration_ms=duration_ms,
        )

    async def _select_top_k_candidates(
        self,
        window: SourceWindow,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
        generation: int,
        start_time: float,
    ) -> TopicCandidateSelection:
        """top-K 选择：小规模走有界 full，大规模使用 fixed K。"""
        assert context.privacy_level is not None
        assert context.chat_type is not None
        # 获取该 scope 的 topic 总数（限制到 activation_threshold +1）
        full_result = await self._catalog.select_full_candidates(
            generation=generation,
            scope_key=context.scope_key,
            privacy_level=context.privacy_level,
            chat_type=context.chat_type,
            resolver_revision=context.resolver_revision,
            max_count=config.activation_threshold + 1,
        )

        topic_count = len(full_result)
        topic_bucket = self._bucket_topic_count(topic_count)

        # 小规模：走有界 full
        if topic_count < config.activation_threshold:
            return await self._select_full_candidates(
                window, context, config, generation, start_time, is_shadow=False
            )

        # 大规模：使用 BM25 + 补足
        return await self._select_bm25_with_fill(
            window, context, config, generation, start_time, topic_bucket
        )

    async def _select_bm25_with_fill(
        self,
        window: SourceWindow,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
        generation: int,
        start_time: float,
        topic_bucket: str,
    ) -> TopicCandidateSelection:
        """BM25 主排序 + 近期/高频补足到 fixed K。"""
        assert context.privacy_level is not None
        assert context.chat_type is not None
        # 1. 构造 query
        window_text = self._formatter.format_conversation(list(window.messages))
        query_result = await build_topic_fts_query(
            text=window_text,
            text_processor=self._text_processor,
            max_chars=config.max_query_chars,
        )

        if not query_result.fts_query:
            # 空 query，走补足
            return await self._select_fill_only(
                context, config, generation, start_time, topic_bucket
            )

        # 2. BM25 主查询
        bm25_candidates = await self._catalog.select_bm25_candidates(
            generation=generation,
            scope_key=context.scope_key,
            privacy_level=context.privacy_level,
            chat_type=context.chat_type,
            resolver_revision=context.resolver_revision,
            fts_query=query_result.fts_query,
            max_count=config.fixed_k,
        )

        bm25_count = len(bm25_candidates)
        fill_candidates: list[dict[str, object]] = []
        excluded_topic_keys = {
            key for row in bm25_candidates if (key := self._candidate_topic_key(row))
        }
        unique_labels = self._deduplicate_labels(
            [row["display_topic"] for row in bm25_candidates]
        )

        # 补足排除 BM25 命中；去重后仍不足 K 时继续请求下一批。
        while len(unique_labels) < config.fixed_k:
            needed = config.fixed_k - len(unique_labels)
            batch = await self._catalog.select_recent_frequent_candidates(
                generation=generation,
                scope_key=context.scope_key,
                privacy_level=context.privacy_level,
                chat_type=context.chat_type,
                resolver_revision=context.resolver_revision,
                max_count=needed,
                overfetch_factor=config.overfetch_factor,
                exclude_topic_keys=excluded_topic_keys,
            )
            if not batch:
                break
            added = 0
            for row in batch:
                key = self._candidate_topic_key(row)
                if not key or key in excluded_topic_keys:
                    continue
                excluded_topic_keys.add(key)
                fill_candidates.append(row)
                added += 1
            if not added:
                break
            unique_labels = self._deduplicate_labels(
                [row["display_topic"] for row in bm25_candidates + fill_candidates]
            )

        unique_labels = unique_labels[: config.fixed_k]
        candidate_shortfall = len(unique_labels) < config.fixed_k

        # 5. Token 预算检查：硬预算超限返回空候选，不软截断成伪 top-K
        # full 路径同样遵循硬预算约束。
        estimated_tokens = self._estimate_tokens(unique_labels)
        budget_reason = ""
        if estimated_tokens > config.max_full_prompt_tokens:
            return self._baseline_result(
                mode=TopicCandidateMode.TOP_K,
                reason="token_budget_exceeded",
                catalog_status="ready",
                topic_count_bucket=topic_bucket,
                candidate_count=len(unique_labels),
                bm25_hit_count=bm25_count,
                recent_fill_count=len(fill_candidates),
                budget_reason="token_exceeded",
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        duration_ms = (time.perf_counter() - start_time) * 1000

        return TopicCandidateSelection(
            labels=tuple(unique_labels),
            source_provenance_complete=True,
            mode=TopicCandidateMode.TOP_K,
            effective_mode=TopicCandidateMode.TOP_K,
            catalog_status="ready",
            topic_count_bucket=topic_bucket,
            candidate_count=len(unique_labels),
            bm25_hit_count=bm25_count,
            recent_fill_count=len(fill_candidates),
            identity_drop_count=0,
            budget_reason=budget_reason,
            reason_code=(
                "candidate_shortfall" if candidate_shortfall else "top_k_success"
            ),
            selector_duration_ms=duration_ms,
        )

    async def _select_fill_only(
        self,
        context: TopicCandidateContext,
        config: CandidateReuseConfig,
        generation: int,
        start_time: float,
        topic_bucket: str,
    ) -> TopicCandidateSelection:
        """只使用近期/高频补足（query 为空时）。"""
        assert context.privacy_level is not None
        assert context.chat_type is not None
        fill_candidates = await self._catalog.select_recent_frequent_candidates(
            generation=generation,
            scope_key=context.scope_key,
            privacy_level=context.privacy_level,
            chat_type=context.chat_type,
            resolver_revision=context.resolver_revision,
            max_count=config.fixed_k,
            overfetch_factor=config.overfetch_factor,
        )

        labels = tuple(
            self._deduplicate_labels([row["display_topic"] for row in fill_candidates])
        )
        if not labels:
            return self._baseline_result(
                mode=config.mode,
                reason="no_fill_candidates",
                catalog_status="ready",
                topic_count_bucket=topic_bucket,
                duration_ms=(time.perf_counter() - start_time) * 1000,
            )

        duration_ms = (time.perf_counter() - start_time) * 1000
        return TopicCandidateSelection(
            labels=labels,
            source_provenance_complete=True,
            mode=config.mode,
            effective_mode=TopicCandidateMode.TOP_K,
            catalog_status="ready",
            topic_count_bucket=topic_bucket,
            candidate_count=len(labels),
            bm25_hit_count=0,
            recent_fill_count=len(fill_candidates),
            identity_drop_count=0,
            budget_reason="",
            reason_code=(
                "candidate_shortfall" if len(labels) < config.fixed_k else "fill_only"
            ),
            selector_duration_ms=duration_ms,
        )

    @staticmethod
    def _candidate_topic_key(row: dict[str, object]) -> str:
        """返回稳定 topic key；旧 mock 行缺 key 时由 label 规范化兜底。"""
        topic_key = row.get("topic_key")
        if isinstance(topic_key, str) and topic_key.strip():
            return topic_key.strip()
        label = row.get("display_topic")
        if not isinstance(label, str):
            return ""
        return unicodedata.normalize("NFKC", label).strip().casefold()

    def _deduplicate_labels(self, labels: list[str]) -> list[str]:
        """按 NFKC 规范键去重。"""
        seen: set[str] = set()
        unique: list[str] = []
        for label in labels:
            key = unicodedata.normalize("NFKC", label).strip().casefold()
            if key and key not in seen:
                seen.add(key)
                unique.append(label)
        return unique

    def _estimate_tokens(self, labels: tuple[str, ...] | list[str]) -> int:
        """简单的 token 估计：平均3字符/token。"""
        total_chars = sum(len(label) for label in labels)
        return max(1, total_chars // 3)

    def _baseline_result(
        self,
        mode: str,
        reason: str,
        catalog_status: str = "unavailable",
        topic_count_bucket: str = "unknown",
        candidate_count: int = 0,
        bm25_hit_count: int = 0,
        recent_fill_count: int = 0,
        budget_reason: str = "",
        duration_ms: float = 0.0,
    ) -> TopicCandidateSelection:
        """构造 baseline 空候选结果。"""
        return TopicCandidateSelection(
            labels=(),
            source_provenance_complete=None,
            mode=mode,
            effective_mode=TopicCandidateMode.OFF,
            catalog_status=catalog_status,
            topic_count_bucket=topic_count_bucket,
            candidate_count=candidate_count,
            bm25_hit_count=bm25_hit_count,
            recent_fill_count=recent_fill_count,
            identity_drop_count=0,
            budget_reason=budget_reason,
            reason_code=reason,
            selector_duration_ms=duration_ms,
        )

    @staticmethod
    def _bucket_topic_count(count: int) -> str:
        """将 topic 数量映射到固定桶。"""
        if count == 0:
            return "0"
        if count <= 8:
            return "1-8"
        if count <= 16:
            return "9-16"
        if count <= 32:
            return "17-32"
        if count <= 64:
            return "33-64"
        if count <= 128:
            return "65-128"
        if count <= 256:
            return "129-256"
        return "257+"


__all__ = ["TopicCandidateSelector"]
