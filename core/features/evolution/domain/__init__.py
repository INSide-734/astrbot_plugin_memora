"""记忆演化 feature 的领域模型。"""

from typing import Literal

from pydantic import BaseModel, Field

from .models import (
    DerivedApplyPlan,
    DerivedState,
    EvolutionProposal,
    EvolutionSignal,
    ExpansionBudget,
    GateDecision,
    JobClaim,
    JobSpec,
    JobState,
    MemoryEvolutionJob,
    MemoryProjectionProposal,
    MemoryRelationProposal,
    MemorySourceRef,
    ProjectionBundle,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
    RetrySpec,
    ScopeContext,
)


class MemoryEvolutionConfig(BaseModel):
    """记忆演化后台任务与派生检索配置。"""

    enabled: bool = Field(default=False, description="是否启用记忆演化功能")
    mode: Literal["disabled", "shadow", "readonly", "active"] = Field(
        default="disabled",
        description="记忆演化运行模式：disabled、shadow、readonly 或 active",
    )
    trigger_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="触发记忆演化的最低重要性或置信度阈值",
    )
    batch_size: int = Field(
        default=16,
        ge=1,
        le=100,
        description="单个演化任务读取的候选记忆数量",
    )
    candidate_limit: int = Field(
        default=16,
        ge=1,
        le=100,
        description="单个种子允许扩展的关系候选数量",
    )
    max_pending_jobs: int = Field(
        default=100,
        ge=0,
        le=10_000,
        description="待处理演化任务的数量上限",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=20,
        description="单个演化任务的最大重试次数",
    )
    lease_seconds: int = Field(
        default=120,
        ge=1,
        le=3_600,
        description="后台 worker 持有任务租约的秒数",
    )
    retry_base_delay_seconds: int = Field(
        default=10,
        ge=1,
        le=3_600,
        description="任务重试的基础延迟秒数",
    )
    consolidation_debounce_seconds: int = Field(
        default=60,
        ge=1,
        le=86_400,
        description="同一记忆演化任务的合并去抖时间窗口（秒）",
    )
    max_input_chars: int = Field(
        default=12_000,
        ge=1,
        le=100_000,
        description="提交给演化模型的证据最大字符数",
    )
    max_output_relations: int = Field(
        default=16,
        ge=0,
        le=64,
        description="单次演化最多生成的关系数量",
    )
    max_output_projections: int = Field(
        default=4,
        ge=0,
        le=16,
        description="单次演化最多生成的投影数量",
    )
    max_query_expansions: int = Field(
        default=8,
        ge=0,
        le=100,
        description="单次查询最多追加的派生记忆数量",
    )
    projection_budget_chars: int = Field(
        default=1_600,
        ge=1,
        le=10_000,
        description="单个投影摘要允许使用的最大字符数",
    )
    require_review_for_high_impact: bool = Field(
        default=True,
        description="高影响关系是否必须经过人工复核",
    )
    auto_active_relation_types: list[str] = Field(
        default_factory=lambda: ["same_episode", "supports", "related"],
        description="允许达到阈值后自动激活的低影响关系类型",
    )


__all__ = [
    "DerivedApplyPlan",
    "DerivedState",
    "EvolutionProposal",
    "EvolutionSignal",
    "ExpansionBudget",
    "GateDecision",
    "JobClaim",
    "JobSpec",
    "JobState",
    "MemoryProjectionProposal",
    "MemoryEvolutionJob",
    "MemoryEvolutionConfig",
    "MemoryRelationProposal",
    "MemorySourceRef",
    "ProjectionType",
    "ProjectionBundle",
    "ProjectionView",
    "ProjectionSourceView",
    "RelationType",
    "RelationView",
    "RetrySpec",
    "ScopeContext",
]
