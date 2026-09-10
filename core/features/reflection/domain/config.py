"""反思 feature 的配置模型。"""

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

_CANONICAL_BUCKETS = frozenset(("tiny", "small", "medium", "large", "xlarge", "huge"))


class BucketOverride(BaseModel):
    """单个规模桶的配置覆盖。

    允许为特定规模桶覆盖全局 mode 和 fixed_k。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["off", "observe", "full", "top_k"] = Field(
        description="此桶的候选复用模式"
    )
    fixed_k: int | None = Field(
        default=None,
        ge=1,
        le=24,
        description="top_k 模式下的 K 值；None 时使用全局 fixed_k",
    )

    @model_validator(mode="after")
    def _validate_mode_k(self) -> "BucketOverride":
        """top_k 覆盖必须携带明确 K，避免运行时隐式继承。"""
        if self.mode == "top_k" and self.fixed_k is None:
            raise ValueError("top_k 桶覆盖必须指定 fixed_k")
        return self


class CandidateReuseConfig(BaseModel):
    """话题候选复用的不可变配置快照。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        validate_default=True,
    )

    mode: Literal["off", "observe", "full", "top_k"] = Field(
        default="observe", description="候选复用模式"
    )
    activation_threshold: int = Field(
        default=32,
        ge=3,
        le=100,
        description="catalog 中话题数量达到此值时才激活重用",
    )
    fixed_k: int = Field(
        default=8,
        ge=1,
        le=24,
        description="top_k 模式下重用的话题数量",
    )
    max_full_topics: int = Field(
        default=32,
        ge=1,
        le=50,
        description="prompt 中包含完整 scope 描述的话题数量上限",
    )
    max_full_prompt_tokens: int = Field(
        default=256,
        ge=50,
        le=2000,
        description="完整话题描述部分的 token 预算上限",
    )
    max_query_chars: int = Field(
        default=2000,
        ge=1,
        le=10000,
        description="候选查询文本的最大字符数",
    )
    overfetch_factor: Literal[3] = Field(
        default=3,
        description="补足扫描倍数，第一阶段固定为 3",
    )
    metrics_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description="候选观测数据保留天数",
    )
    observe_max_candidates: int = Field(
        default=32,
        ge=1,
        le=512,
        validation_alias=AliasChoices(
            "observe_max_candidates",
            "observe_shadow_max_candidates",
            "shadow_max_candidates",
            "shadow_candidate_limit",
        ),
        description="observe 影子计算的候选数量上限",
    )
    observe_max_rows: int = Field(
        default=96,
        ge=1,
        le=4096,
        validation_alias=AliasChoices(
            "observe_max_rows",
            "observe_shadow_max_rows",
            "shadow_max_rows",
            "shadow_row_limit",
        ),
        description="observe 影子扫描的行数上限",
    )
    observe_max_duration_ms: int = Field(
        default=250,
        ge=1,
        le=60000,
        validation_alias=AliasChoices(
            "observe_max_duration_ms",
            "observe_shadow_timeout_ms",
            "observe_timeout_ms",
            "shadow_timeout_ms",
            "shadow_max_duration_ms",
        ),
        description="observe 影子计算的耗时上限",
    )
    bucket_overrides: dict[str, BucketOverride] = Field(
        default_factory=dict,
        description="按规模桶覆盖全局配置；键为桶名（tiny/small/medium/large/huge）",
    )

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "CandidateReuseConfig":
        """校验 fixed K、桶覆盖、full 上限和 observe 影子预算之间的关系。"""

        if self.fixed_k > self.max_full_topics:
            raise ValueError("fixed_k 不能大于 max_full_topics")
        if self.activation_threshold > self.max_full_topics:
            raise ValueError("activation_threshold 不能大于 max_full_topics")
        unknown_buckets = set(self.bucket_overrides) - _CANONICAL_BUCKETS
        if unknown_buckets:
            raise ValueError("bucket_overrides 包含未知规模桶")
        for bucket, override in self.bucket_overrides.items():
            if override.fixed_k is not None and override.fixed_k > self.max_full_topics:
                raise ValueError(
                    f"bucket_overrides.{bucket}.fixed_k 不能大于 max_full_topics"
                )
        if self.observe_max_candidates > self.observe_max_rows:
            raise ValueError("observe_max_candidates 不能大于 observe_max_rows")
        if self.overfetch_factor != 3:
            raise ValueError("overfetch_factor 第一阶段必须为 3")
        return self

    @property
    def observe_shadow_max_candidates(self) -> int:
        """返回 observe 影子候选数量上限。"""

        return self.observe_max_candidates

    @property
    def observe_shadow_max_rows(self) -> int:
        """返回 observe 影子扫描行数上限。"""

        return self.observe_max_rows

    @property
    def observe_shadow_timeout_ms(self) -> int:
        """返回 observe 影子耗时上限。"""

        return self.observe_max_duration_ms

    @property
    def shadow_candidate_limit(self) -> int:
        """返回兼容命名的影子候选上限。"""

        return self.observe_max_candidates

    @property
    def shadow_row_limit(self) -> int:
        """返回兼容命名的影子扫描上限。"""

        return self.observe_max_rows

    @property
    def shadow_timeout_ms(self) -> int:
        """返回兼容命名的影子耗时上限。"""

        return self.observe_max_duration_ms

    def get_bucket_config(
        self, bucket: str
    ) -> tuple[Literal["off", "observe", "full", "top_k"], int]:
        """获取特定规模桶的有效配置。

        参数:
            bucket: 规模桶名称（tiny/small/medium/large/xlarge/huge）

        返回:
            (mode, fixed_k) 元组
        """
        override = self.bucket_overrides.get(bucket)
        if override is not None:
            # 使用桶覆盖的 mode，K 值优先用 override.fixed_k
            effective_k = (
                override.fixed_k if override.fixed_k is not None else self.fixed_k
            )
            return override.mode, effective_k
        # 回落全局配置
        return self.mode, self.fixed_k


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
    catalog_reconcile_interval_seconds: int = Field(
        default=60,
        ge=0,
        le=86400,
        description="目录周期收敛间隔秒数；0 表示禁用运行期周期 reconcile",
    )
    candidate_reuse: CandidateReuseConfig = Field(
        default_factory=CandidateReuseConfig, description="候选话题重用配置"
    )


__all__ = [
    "BucketOverride",
    "CandidateReuseConfig",
    "LegacyBackfillConfig",
    "ReflectionEngineConfig",
    "StrategyBConfig",
    "StrategyCConfig",
    "StrategyDConfig",
    "TopicSegmentationConfig",
]
