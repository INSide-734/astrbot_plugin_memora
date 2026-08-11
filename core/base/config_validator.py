"""
config_validator.py - 配置验证模块
提供配置验证和默认值管理功能。
"""

from pydantic import Field

from ..platform.config.feature_contributions import (
    AtomQualityFilterConfig,
    FilteringConfig,
    ForgettingAgentConfig,
    FusionStrategyConfig,
    GraphMemoryConfig,
    ImportanceDecayConfig,
    MemoryEvolutionConfig,
    MigrationSettings,
    RecallEngineConfig,
    ReflectionEngineConfig,
    RerankerConfig,
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
