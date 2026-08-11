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
from .feature_config import (
    AgentToolsConfig,
    DashboardConfig,
    JargonConfig,
    UpdateSettings,
)
from .runtime_feature_config import RuntimeFeatureConfigSections


class SessionManagerConfig(BaseModel):
    """会话管理器配置"""

    max_sessions: int = Field(
        default=100, ge=1, le=10000, description="最大会话缓存数量"
    )
    session_ttl: int = Field(
        default=3600, ge=60, le=86400, description="会话生存时间（秒）"
    )
    context_window_size: int = Field(
        default=50, ge=1, le=1000, description="上下文窗口大小"
    )
    enable_full_group_capture: bool = Field(
        default=True, description="是否捕获群聊中的所有消息(包括非@Bot的消息)"
    )
    max_messages_per_session: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="单会话最大消息数量(超出后自动删除旧消息)",
    )
    cleanup_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
        description="历史消息超过上限后每次批量删除的旧已总结消息数",
    )


class FusionStrategyConfig(BaseModel):
    """结果融合策略配置"""

    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF参数k")


class CostControlConfig(BaseModel):
    """成本控制配置 — 统一管理高成本 LLM 功能的启用/降级策略。"""

    mode: str = Field(
        default="balanced",
        description="成本模式: balanced(默认，禁止额外LLM调用), low_cost(最小化token), quality(允许高成本路径)",
    )
    max_extra_llm_calls_per_turn: int = Field(
        default=0,
        ge=0,
        le=10,
        description="每轮额外 LLM 调用上限。balanced/low_cost 下默认 0",
    )
    allow_llm_reranker_in_passive_recall: bool = Field(
        default=False, description="是否允许被动召回中触发 LLM reranker"
    )
    allow_llm_topic_strategy_d: bool = Field(
        default=False, description="是否允许两阶段 LLM 话题分割（strategy D）"
    )
    max_reflection_parallel_llm_calls: int = Field(
        default=2, ge=1, le=8, description="反思时并行 LLM 调用上限"
    )
    llm_reranker_min_candidates: int = Field(
        default=12, ge=1, le=50, description="触发 LLM reranker 的最小候选数"
    )
    llm_reranker_prompt_chars: int = Field(
        default=3000, ge=500, le=10000, description="LLM reranker prompt 最大字符数"
    )


class SecurityConfig(BaseModel):
    """Prompt 防护与 LLM 输出护栏配置。"""

    prompt_protection_enabled: bool = Field(
        default=True, description="是否对注入的记忆上下文启用提示词保护包装"
    )
    sanitize_llm_response: bool = Field(
        default=True, description="是否在助手回复落库前清理泄露的内部提示词片段"
    )
    guardrails_enabled: bool = Field(
        default=True, description="是否启用记忆抽取输出的结构化护栏校验"
    )
    double_check_enabled: bool = Field(
        default=True, description="是否启用提示词保护与回复清洗的二次校验"
    )
    wrapper_template_index: int = Field(
        default=0, ge=0, le=10, description="提示词保护包装模板索引"
    )
    strict_mode: bool = Field(
        default=False,
        description="严格模式下安全组件失败会跳过注入或落库，而不是降级放行",
    )


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
