"""
config_validator.py - 配置验证模块
提供配置验证和默认值管理功能。
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..platform.config.feature_contributions import (
    AtomQualityFilterConfig,
    ForgettingAgentConfig,
    GraphMemoryConfig,
    ImportanceDecayConfig,
    MemoryEvolutionConfig,
    ReflectionEngineConfig,
    TopicSegmentationConfig,
)
from ..platform.config.feature_contributions import (
    LegacyBackfillConfig as LegacyBackfillConfig,
)
from ..platform.config.feature_contributions import (
    StrategyBConfig as StrategyBConfig,
)
from ..platform.config.feature_contributions import (
    StrategyCConfig as StrategyCConfig,
)
from ..platform.config.feature_contributions import (
    StrategyDConfig as StrategyDConfig,
)
from .feature_config import (
    AgentToolsConfig,
    DashboardConfig,
    JargonConfig,
    UpdateSettings,
)
from .runtime_feature_config import RuntimeFeatureConfigSections

PresetName = Literal["tool_first", "low_cost", "balanced", "quality"]


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


class RecallEngineConfig(BaseModel):
    """回忆引擎配置"""

    top_k: int = Field(
        default=5, ge=0, le=50, description="返回记忆数量。设为 0 则跳过自动召回和注入"
    )
    pre_llm_soft_budget_ms: int = Field(
        default=800,
        ge=0,
        le=5000,
        description="LLM 请求前被动召回的软预算毫秒数；0 表示关闭",
    )
    max_k: int = Field(
        default=10, ge=1, le=50, description="Agent 主动检索时允许的最大返回数量"
    )
    importance_weight: float = Field(
        default=1.0, ge=0.0, le=10.0, description="重要性权重"
    )
    fallback_to_vector: bool = Field(default=True, description="是否启用向量检索回退")
    injection_routing_mode: Literal["manual", "auto", "hybrid"] = "manual"
    injection_manual_preset: PresetName = "balanced"
    injection_auto_fallback_preset: PresetName = "balanced"
    injection_hybrid_base_preset: PresetName = "balanced"
    injection_hybrid_min_preset: PresetName = "low_cost"
    injection_hybrid_max_preset: PresetName = "quality"
    injection_delivery_override: Literal[
        "auto",
        "extra_user_content",
        "user_message_before",
        "user_message_after",
        "fake_tool_call",
        "fake_tool_call_deepseek_v4",
    ] = "auto"
    injection_preset_overrides_enabled: bool = False
    injection_decision_retention_days: Literal[0, 7, 30, 90, 180] = 30
    injection_decision_max_rows: int = Field(
        default=100_000,
        ge=1_000,
        le=1_000_000,
        description="注入决策记录最大保留行数",
    )
    auto_remove_injected: bool = Field(
        default=True, description="是否自动删除对话历史中已注入的记忆片段"
    )
    inject_with_recent_context: bool = Field(
        default=False,
        description="启用后使用最近2轮对话作为扩展查询关键词，提升检索精准度",
    )
    search_cache_enabled: bool = Field(
        default=True, description="是否启用短期检索结果缓存"
    )
    search_cache_ttl_seconds: float = Field(
        default=45.0, ge=0.0, le=600.0, description="检索缓存 TTL 秒数"
    )
    search_cache_max_size: int = Field(
        default=256, ge=0, le=10000, description="检索缓存最大条目数"
    )
    # 请求级会话缓存 — 消除同一请求内 Bridge→RecallHandler 重复检索
    session_cache_enabled: bool = Field(
        default=True, description="是否启用请求级会话缓存（消除重复检索）"
    )
    session_cache_ttl_seconds: float = Field(
        default=10.0, ge=0.0, le=120.0, description="请求级会话缓存 TTL 秒数"
    )
    id_cache_size: int = Field(default=1000, description="向量 ID 缓存大小")
    stopwords_path: str = Field(default="", description="自定义停用词文件路径")
    query_rewrite_enabled: bool = Field(
        default=True, description="是否启用语义查询改写"
    )
    privacy_filter_enabled: bool = Field(
        default=True, description="是否启用隐私记忆过滤"
    )
    # 链式扩展 — R2 多跳图扩展
    max_chain_hops: int = Field(
        default=3, ge=0, le=3, description="链式扩展最大跳数。0 禁用"
    )
    chain_hop_decay: float = Field(
        default=0.7, ge=0.0, le=1.0, description="逐跳衰减系数"
    )
    chain_graph_expansion_enabled: bool = Field(
        default=True, description="是否启用图边多跳扩展"
    )
    chain_topic_expansion_enabled: bool = Field(
        default=True, description="是否启用话题关联多跳扩展"
    )
    # 测试效应 — 召回成功后强化记忆访问时间
    testing_effect_async: bool = Field(
        default=True, description="测试效应是否异步执行（不阻塞检索热路径）"
    )
    testing_effect_top_k: int = Field(
        default=5, ge=1, le=50, description="测试效应处理的 Top-K 记忆数"
    )
    # === 注入预算 — 控制每轮请求中注入的记忆上下文总量 ===
    injection_budget_chars: int = Field(
        default=0,
        ge=0,
        le=10000,
        description="普通记忆预算覆盖；0 使用预设默认值",
    )
    injection_memory_max_chars: int = Field(
        default=0,
        ge=0,
        le=2000,
        description="单条记忆长度覆盖；0 使用预设默认值",
    )
    injection_metadata_max_chars: int = Field(
        default=0,
        ge=0,
        le=500,
        description="单条元数据长度覆盖；0 使用预设默认值",
    )
    injection_include_key_facts: bool = Field(
        default=True, description="是否在注入中显示 key_facts"
    )
    injection_include_topics: bool = Field(
        default=True, description="是否在注入中显示 topics"
    )
    injection_include_participants: bool = Field(
        default=False, description="是否在注入中显示 participants"
    )
    injection_compact_header: bool = Field(
        default=True, description="是否使用紧凑版注入 header/footer（省略英文安全规则）"
    )
    cognitive_context_budget_chars: int = Field(
        default=300,
        ge=0,
        le=2000,
        description="认知上下文（黑话/表达/好感度）预算字符数",
    )
    proactive_plan_budget_chars: int = Field(
        default=240, ge=0, le=1000, description="前瞻提醒预算字符数"
    )
    serial_position_enabled: bool = Field(
        default=True, description="是否启用序列位置效应"
    )
    spontaneous_recall_enabled: bool = Field(
        default=True, description="是否启用自发回忆"
    )
    spontaneous_recall_probability: float = Field(
        default=0.06, ge=0.0, le=1.0, description="自发回忆触发概率"
    )
    spontaneous_recall_k: int = Field(default=2, description="自发回忆返回条数")
    prospective_recall_enabled: bool = Field(
        default=True, description="是否启用前瞻记忆"
    )
    prospective_lookahead_hours: float = Field(
        default=24.0, description="前瞻记忆扫描窗口"
    )
    prospective_recall_k: int = Field(default=3, description="前瞻记忆返回条数")
    narrative_coherence_enabled: bool = Field(
        default=True, description="是否启用叙事连贯性"
    )
    interest_boost_enabled: bool = Field(
        default=True, description="是否启用兴趣记忆显著性"
    )

    @model_validator(mode="after")
    def validate_hybrid_order(self) -> "RecallEngineConfig":
        """确保 Hybrid 策略的最小、基准和最大预设按等级递增。"""
        ranks = {"tool_first": 0, "low_cost": 1, "balanced": 2, "quality": 3}
        if not (
            ranks[self.injection_hybrid_min_preset]
            <= ranks[self.injection_hybrid_base_preset]
            <= ranks[self.injection_hybrid_max_preset]
        ):
            raise ValueError("hybrid preset order must satisfy min <= base <= max")
        return self


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


class ProviderConfig(BaseModel):
    """Provider 配置"""

    embedding_provider_id: str | None = Field(
        default="", description="嵌入模型 Provider ID"
    )
    llm_provider_id: str | None = Field(default="", description="语言模型 Provider ID")


class MigrationSettings(BaseModel):
    """数据库迁移设置"""

    auto_migrate: bool = Field(default=True, description="是否启用自动迁移")
    create_backup: bool = Field(default=True, description="迁移前是否创建备份")


class IndexRebuildSettings(BaseModel):
    """索引重建设置"""

    batch_size: int = Field(default=50, ge=1, le=500, description="重建读取批量")
    embedding_batch_size: int = Field(
        default=8, ge=1, le=256, description="Embedding 请求批量"
    )
    tasks_limit: int = Field(default=1, ge=1, le=8, description="Embedding 并发上限")
    max_retries: int = Field(default=5, ge=1, le=8, description="批次最大重试次数")
    retry_base_delay: float = Field(
        default=30.0, ge=0.0, le=60.0, description="重试基础等待秒数"
    )
    batch_delay: float = Field(
        default=5.0, ge=0.0, le=10.0, description="读取批次间隔秒数"
    )
    request_delay: float = Field(
        default=5.0, ge=0.0, le=60.0, description="Embedding 请求间隔秒数"
    )
    max_failure_ratio: float = Field(
        default=0.02, ge=0.0, le=1.0, description="允许切换的最大失败比例"
    )


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
