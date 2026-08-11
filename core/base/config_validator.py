"""
config_validator.py - 配置验证模块
提供配置验证和默认值管理功能。
"""

from pydantic import BaseModel, Field

from ..platform.config.feature_contributions import (
    AtomQualityFilterConfig,
    ForgettingAgentConfig,
    GraphMemoryConfig,
    ImportanceDecayConfig,
    MemoryEvolutionConfig,
    MigrationSettings,
    RecallEngineConfig,
    ReflectionEngineConfig,
    SessionManagerConfig,
    TopicSegmentationConfig,
)
from ..platform.config.feature_contributions import (
    LegacyBackfillConfig as LegacyBackfillConfig,
)
from ..platform.config.feature_contributions import PresetName as PresetName
from ..platform.config.feature_contributions import (
    StrategyBConfig as StrategyBConfig,
)
from ..platform.config.feature_contributions import (
    StrategyCConfig as StrategyCConfig,
)
from ..platform.config.feature_contributions import (
    StrategyDConfig as StrategyDConfig,
)
from ..platform.config.provider_config import ProviderConfig
from ..platform.config.rebuild_config import IndexRebuildSettings
from ..platform.config.security_config import SecurityConfig
from ..shared.cost_control import CostControlConfig
from .feature_config import (
    AgentToolsConfig,
    DashboardConfig,
    JargonConfig,
    UpdateSettings,
)
from .runtime_feature_config import RuntimeFeatureConfigSections


class FusionStrategyConfig(BaseModel):
    """结果融合策略配置"""

    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF参数k")


class FilteringConfig(BaseModel):
    """过滤配置"""

    use_persona_filtering: bool = Field(default=True, description="是否使用人格过滤")
    use_session_filtering: bool = Field(default=True, description="是否使用会话过滤")


class RerankerConfig(BaseModel):
    """重排序器配置 — 检索后对候选记忆进行精细重排序。"""

    enabled: bool = Field(
        default=True, description="是否启用重排序器。关闭后跳过所有重排序步骤"
    )
    strategy: str = Field(
        default="mmr",
        description="重排序策略: mmr(最大边际相关性), embedding_similarity(Embedding相似度), llm(LLM打分—高成本), hybrid(两级排序)",
    )
    mmr_lambda: float = Field(
        default=0.7, ge=0.0, le=1.0, description="MMR 相关性权重。值越高越偏相关性"
    )
    embedding_similarity_lambda: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="query-doc Embedding 余弦相似度融合权重",
    )
    llm_batch_size: int = Field(
        default=10, ge=1, le=50, description="LLM 重排序每批候选记忆数上限"
    )


class MemoraConfig(RuntimeFeatureConfigSections):
    """完整插件配置"""

    debug: bool = Field(
        default=False,
        description="用户问题报告调试模式；只输出隐私安全的结构化诊断事件",
    )

    session_manager: SessionManagerConfig = Field(default_factory=SessionManagerConfig)
    recall_engine: RecallEngineConfig = Field(default_factory=RecallEngineConfig)
    reflection_engine: ReflectionEngineConfig = Field(
        default_factory=ReflectionEngineConfig
    )
    agent_tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    jargon: JargonConfig = Field(default_factory=JargonConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    update_settings: UpdateSettings = Field(default_factory=UpdateSettings)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    forgetting_agent: ForgettingAgentConfig = Field(
        default_factory=ForgettingAgentConfig
    )
    filtering_settings: FilteringConfig = Field(default_factory=FilteringConfig)
    provider_settings: ProviderConfig = Field(default_factory=ProviderConfig)
    migration_settings: MigrationSettings = Field(default_factory=MigrationSettings)
    index_rebuild_settings: IndexRebuildSettings = Field(
        default_factory=IndexRebuildSettings
    )
    graph_memory: GraphMemoryConfig = Field(default_factory=GraphMemoryConfig)
    atom_quality_filter: AtomQualityFilterConfig = Field(
        default_factory=AtomQualityFilterConfig
    )
    fusion_strategy: FusionStrategyConfig = Field(
        default_factory=FusionStrategyConfig, description="结果融合策略配置"
    )
    importance_decay: ImportanceDecayConfig = Field(
        default_factory=ImportanceDecayConfig, description="重要性衰减配置"
    )
    topic_segmentation: TopicSegmentationConfig = Field(
        default_factory=TopicSegmentationConfig, description="话题分割配置"
    )
    reranker: RerankerConfig = Field(
        default_factory=RerankerConfig, description="重排序器配置"
    )
    cost_control: CostControlConfig = Field(
        default_factory=CostControlConfig, description="成本控制配置"
    )
    memory_evolution: MemoryEvolutionConfig = Field(
        default_factory=MemoryEvolutionConfig, description="记忆演化配置"
    )

    model_config = {"extra": "allow"}  # 允许额外字段，向前兼容


# 旧模块继续暴露原有名称，但平台层持有唯一校验编排实现。
from ..platform.config import validation as _platform_validation  # noqa: E402

get_default_config = _platform_validation.get_default_config
merge_config_with_defaults = _platform_validation.merge_config_with_defaults
validate_config = _platform_validation.validate_config
validate_runtime_config_changes = _platform_validation.validate_runtime_config_changes
