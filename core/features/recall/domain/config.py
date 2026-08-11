"""召回 feature 的配置模型。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

PresetName = Literal["tool_first", "low_cost", "balanced", "quality"]


class FilteringConfig(BaseModel):
    """过滤配置"""

    use_persona_filtering: bool = Field(default=True, description="是否使用人格过滤")
    use_session_filtering: bool = Field(default=True, description="是否使用会话过滤")


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
    # 请求级会话缓存用于消除同一请求内 Bridge 到 RecallHandler 的重复检索。
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
    # 链式扩展使用 R2 多跳图扩展。
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
    # 测试效应在召回成功后强化记忆访问时间。
    testing_effect_async: bool = Field(
        default=True, description="测试效应是否异步执行（不阻塞检索热路径）"
    )
    testing_effect_top_k: int = Field(
        default=5, ge=1, le=50, description="测试效应处理的 Top-K 记忆数"
    )
    # 注入预算控制每轮请求中注入的记忆上下文总量。
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


__all__ = ["FilteringConfig", "PresetName", "RecallEngineConfig"]
