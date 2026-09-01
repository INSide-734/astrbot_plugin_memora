"""反思 feature 的配置模型。"""

from pydantic import BaseModel, Field


class ReflectionEngineConfig(BaseModel):
    """反思引擎配置。"""

    summary_trigger_rounds: int = Field(
        default=10, ge=1, le=100, description="触发反思的对话轮次"
    )

    max_parallel_summary_tasks: int = Field(
        default=4, ge=1, le=16, description="全局活动总结窗口上限"
    )
    max_parallel_summary_tasks_per_session: int = Field(
        default=2, ge=1, le=8, description="单会话活动总结窗口上限"
    )


class StrategyBConfig(BaseModel):
    """策略 B 的嵌入聚类参数。"""

    similarity_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="两条 key_fact 的 cosine 相似度阈值"
    )
    min_cluster_size: int = Field(
        default=1, ge=1, le=100, description="每个话题至少包含的 key_fact 数量"
    )
    max_clusters: int = Field(
        default=5, ge=1, le=50, description="单次分割最多产生的话题数量上限"
    )


class StrategyCConfig(BaseModel):
    """策略 C 的话题感知预分块参数。"""

    topic_shift_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="相邻消息语义相似度低于此值时判定为话题边界",
    )
    min_chunk_size: int = Field(
        default=2, ge=1, le=100, description="每个话题块至少包含的消息条数"
    )


class StrategyDConfig(BaseModel):
    """策略 D 的两阶段 LLM 参数。"""

    stage1_max_topics: int = Field(
        default=5, ge=1, le=50, description="Stage 1 LLM 最多识别的话题数量"
    )
    enable_parallel_stage2: bool = Field(
        default=True, description="是否并行执行多个话题的 Stage 2 LLM 调用"
    )


class LegacyBackfillConfig(BaseModel):
    """存量记忆话题分割回填配置。"""

    enabled: bool = Field(default=True, description="是否启用存量回填")
    batch_size: int = Field(default=50, ge=1, le=1000, description="每批回填的记忆数量")
    max_backfill_per_run: int = Field(
        default=500, ge=1, le=10000, description="单次回填任务的最大处理量"
    )


class TopicSegmentationConfig(BaseModel):
    """将 LLM 返回的混合多话题记忆拆分为独立 MemoryAtom。"""

    enabled: bool = Field(default=True, description="是否启用话题分割")
    strategy: str = Field(
        default="a_b_hybrid",
        description="话题分割策略: a_b_hybrid / strategy_a / strategy_b / strategy_c / strategy_d",
    )
    strategy_b: StrategyBConfig = Field(default_factory=StrategyBConfig)
    strategy_c: StrategyCConfig = Field(default_factory=StrategyCConfig)
    strategy_d: StrategyDConfig = Field(default_factory=StrategyDConfig)
    hybrid_fallback_fact_threshold: int = Field(
        default=3, ge=1, le=100, description="Hybrid 策略回退的 fact 数量阈值"
    )
    legacy_backfill: LegacyBackfillConfig = Field(default_factory=LegacyBackfillConfig)


__all__ = [
    "LegacyBackfillConfig",
    "ReflectionEngineConfig",
    "StrategyBConfig",
    "StrategyCConfig",
    "StrategyDConfig",
    "TopicSegmentationConfig",
]
