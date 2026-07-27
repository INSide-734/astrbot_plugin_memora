"""为反思存储准备按话题切分的消息批次。

该模块从 ``ReflectionHandler._prepare_message_batches`` 中拆出，
用于控制处理器体积并提升可测试性。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..base.config_manager import ConfigManager
from ..processors.topic_splitter import (
    TopicChunkingStrategy,
    TwoStageLLMStrategy,
)


class TopicBatchPreparer:
    """根据当前话题分割策略准备消息批次。

    策略 C：基于嵌入检测话题边界并预切分。
    策略 D：先由 LLM 识别话题，再进行分割。
    其他策略：直接返回单一批次。

    参数：
        config_manager: 插件配置管理器。
        memory_engine: 记忆引擎实例，策略 C 中用于 ``embed_texts``。
        memory_processor: 记忆处理器实例，策略 D 中用于 LLM 客户端和格式化器。
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        memory_engine: Any = None,
        memory_processor: Any = None,
    ) -> None:
        self._config_manager = config_manager
        self._memory_engine = memory_engine
        self._memory_processor = memory_processor

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    async def prepare_batches(
        self, history_messages: list, is_group_chat: bool
    ) -> list[list]:
        """将 *history_messages* 切分为按话题组织的批次。

        返回消息子列表组成的列表。当当前策略既不是 C 也不是 D，
        或者当前批次过小不适合继续切分时，返回仅包含完整输入的单元素列表。
        """
        strategy_key = self._config_manager.get(
            "topic_segmentation.strategy", "a_b_hybrid"
        )
        if strategy_key not in ("c", "d"):
            return [list(history_messages)]

        if len(history_messages) < 3:
            return [list(history_messages)]

        topic_cfg = self._build_topic_config()

        if strategy_key == "c":
            return await self._prepare_strategy_c(history_messages, topic_cfg)

        if strategy_key == "d":
            # 成本控制：balanced/low_cost 下 strategy D 自动降级为单批次
            cost_mode = self._config_manager.get("cost_control.mode", "balanced")
            allow_d = self._config_manager.get(
                "cost_control.allow_llm_topic_strategy_d", False
            )
            if cost_mode != "quality" and not allow_d:
                logger.info(
                    f"[CostControl] topic strategy=d 降级为单批次: "
                    f"mode={cost_mode}, allow_llm_topic_strategy_d={allow_d}"
                )
                return [list(history_messages)]
            return await self._prepare_strategy_d(history_messages, topic_cfg)

        return [list(history_messages)]

    # ------------------------------------------------------------------
    # 策略实现
    # ------------------------------------------------------------------

    async def _prepare_strategy_c(
        self, history_messages: list, topic_cfg: dict
    ) -> list[list]:
        """使用基于嵌入的话题边界检测执行预切分。"""
        strat = TopicChunkingStrategy(
            topic_cfg.get("strategy_c", {}),
            embed_fn=getattr(self._memory_engine, "embed_texts", None),
        )
        try:
            return await strat.chunk_messages(history_messages)
        except Exception:
            logger.warning("[话题批次准备器] 策略 C 切分失败，已回退为单批次处理。")
            return [list(history_messages)]

    async def _prepare_strategy_d(
        self, history_messages: list, topic_cfg: dict
    ) -> list[list]:
        """执行两阶段 LLM 话题识别与切分。"""
        formatter = self._memory_processor.conversation_formatter
        conversation_text = formatter.format_conversation(history_messages)
        strat = TwoStageLLMStrategy(
            topic_cfg.get("strategy_d", {}),
            llm_client=self._memory_processor.llm_client_instance,
        )
        try:
            topics = await strat.identify_topics(conversation_text)
        except Exception:
            logger.warning(
                "[话题批次准备器] 策略 D 的第一阶段失败，已回退为单批次处理。"
            )
            return [list(history_messages)]

        if not topics or len(topics) <= 1:
            return [list(history_messages)]

        # 将 line_ranges 转为消息子列表（行号从 1 开始）
        batches: list[list] = []
        for t in topics:
            line_range = t.get("line_range", [])
            if len(line_range) == 2:
                start = max(0, line_range[0] - 1)
                end = min(len(history_messages), line_range[1])
                if end > start:
                    batches.append(history_messages[start:end])
        return batches if batches else [list(history_messages)]

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _build_topic_config(self) -> dict[str, Any]:
        """构建嵌套形式的话题分割配置字典。"""
        c = self._config_manager
        return {
            "enabled": c.get("topic_segmentation.enabled"),
            "strategy": c.get("topic_segmentation.strategy"),
            "strategy_b": {
                "similarity_threshold": c.get(
                    "topic_segmentation.strategy_b.similarity_threshold"
                ),
                "min_cluster_size": c.get(
                    "topic_segmentation.strategy_b.min_cluster_size"
                ),
                "max_clusters": c.get("topic_segmentation.strategy_b.max_clusters"),
            },
            "strategy_c": {
                "topic_shift_threshold": c.get(
                    "topic_segmentation.strategy_c.topic_shift_threshold"
                ),
                "min_chunk_size": c.get("topic_segmentation.strategy_c.min_chunk_size"),
            },
            "strategy_d": {
                "stage1_max_topics": c.get(
                    "topic_segmentation.strategy_d.stage1_max_topics"
                ),
                "enable_parallel_stage2": c.get(
                    "topic_segmentation.strategy_d.enable_parallel_stage2"
                ),
            },
            "hybrid_fallback_fact_threshold": c.get(
                "topic_segmentation.hybrid_fallback_fact_threshold"
            ),
            "legacy_backfill": {
                "enabled": c.get("topic_segmentation.legacy_backfill.enabled"),
                "batch_size": c.get("topic_segmentation.legacy_backfill.batch_size"),
                "max_backfill_per_run": c.get(
                    "topic_segmentation.legacy_backfill.max_backfill_per_run"
                ),
            },
        }


__all__ = ["TopicBatchPreparer"]
