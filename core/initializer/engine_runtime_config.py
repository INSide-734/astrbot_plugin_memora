"""ConfigManager 到 MemoryEngine 的唯一显式运行时投影。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..base.config_runtime_effects import RuntimeConfigEffect


class ConfigReader(Protocol):
    """声明运行时投影所需的最小点路径读取接口。"""

    def get(self, path: str, default: Any = None) -> Any:
        """读取点路径配置，缺失时返回默认值。"""


@dataclass(frozen=True, slots=True)
class EngineRuntimeField:
    """描述一个公开配置叶到引擎字典键的单向映射。"""

    source_path: str
    target_key: str
    default: Any
    effect: RuntimeConfigEffect = RuntimeConfigEffect.RESTART


def _field(
    source_path: str,
    target_key: str,
    default: Any,
    effect: RuntimeConfigEffect = RuntimeConfigEffect.RESTART,
) -> EngineRuntimeField:
    """用紧凑写法声明一个不可变运行时映射。"""

    return EngineRuntimeField(source_path, target_key, default, effect)


ENGINE_RUNTIME_FIELDS: tuple[EngineRuntimeField, ...] = (
    _field("fusion_strategy.rrf_k", "rrf_k", 60),
    _field("importance_decay.decay_rate", "decay_rate", 0.01),
    _field(
        "importance_decay.access_decay_window_days",
        "access_decay_window_days",
        30.0,
    ),
    _field(
        "importance_decay.access_decay_max_count",
        "access_decay_max_count",
        10,
    ),
    _field(
        "importance_decay.access_count_decay_multiplier",
        "access_count_decay_multiplier",
        0.5,
    ),
    _field("recall_engine.importance_weight", "importance_weight", 1.0),
    _field("recall_engine.search_cache_enabled", "search_cache_enabled", True),
    _field(
        "recall_engine.search_cache_ttl_seconds",
        "search_cache_ttl_seconds",
        45.0,
    ),
    _field("recall_engine.search_cache_max_size", "search_cache_max_size", 256),
    _field("recall_engine.fallback_to_vector", "fallback_enabled", True),
    _field(
        "forgetting_agent.cleanup_days_threshold",
        "cleanup_days_threshold",
        30,
    ),
    _field(
        "forgetting_agent.cleanup_importance_threshold",
        "cleanup_importance_threshold",
        0.3,
    ),
    _field("forgetting_agent.auto_cleanup_enabled", "auto_cleanup_enabled", True),
    _field("graph_memory.document_route_weight", "document_route_weight", 0.65),
    _field("graph_memory.graph_route_weight", "graph_route_weight", 0.35),
    _field("graph_memory.cross_route_bonus", "cross_route_bonus", 0.08),
    _field("graph_memory.expansion_limit", "graph_expansion_limit", 24),
    _field("graph_memory.expansion_hops", "graph_expansion_hops", 1),
    _field("graph_memory.second_hop_weight", "graph_second_hop_weight", 0.4),
    _field(
        "graph_memory.dynamic_route_weighting",
        "dynamic_route_weighting",
        True,
    ),
    _field("graph_memory.max_topics_per_memory", "graph_max_topics", 6),
    _field(
        "graph_memory.max_participants_per_memory",
        "graph_max_participants",
        8,
    ),
    _field("graph_memory.max_facts_per_memory", "graph_max_facts", 8),
    _field("graph_memory.atom_enabled", "atom_enabled", True),
    _field(
        "graph_memory.atom_maintenance_interval_hours",
        "atom_maintenance_interval_hours",
        24.0,
    ),
    _field("graph_memory.atom_forget_delay_days", "atom_forget_delay_days", 7.0),
    _field("graph_memory.atom_purge_delay_days", "atom_purge_delay_days", 30.0),
    _field(
        "atom_quality_filter.atom_quality_filter_enabled",
        "atom_quality_filter_enabled",
        True,
    ),
    _field("atom_quality_filter.atom_min_confidence", "atom_min_confidence", 0.65),
    _field("atom_quality_filter.atom_min_importance", "atom_min_importance", 0.3),
    _field(
        "atom_quality_filter.atom_min_content_length",
        "atom_min_content_length",
        5,
    ),
    _field(
        "atom_quality_filter.atom_info_check_enabled",
        "atom_info_check_enabled",
        True,
    ),
    _field(
        "atom_quality_filter.atom_probationary_enabled",
        "atom_probationary_enabled",
        True,
    ),
    _field(
        "atom_quality_filter.atom_probationary_ttl_days",
        "atom_probationary_ttl_days",
        3.0,
    ),
    _field("atom_quality_filter.atom_dedup_enabled", "atom_dedup_enabled", True),
    _field("atom_quality_filter.atom_dedup_threshold", "atom_dedup_threshold", 0.7),
    _field(
        "atom_quality_filter.atom_cold_storage_enabled",
        "atom_cold_storage_enabled",
        True,
    ),
    _field(
        "atom_quality_filter.atom_cold_days_threshold",
        "atom_cold_days_threshold",
        14.0,
    ),
    _field(
        "atom_quality_filter.atom_cold_max_importance",
        "atom_cold_max_importance",
        0.4,
    ),
    _field("index_rebuild_settings.batch_size", "index_rebuild_batch_size", 50),
    _field(
        "index_rebuild_settings.embedding_batch_size",
        "index_rebuild_embedding_batch_size",
        8,
    ),
    _field(
        "index_rebuild_settings.tasks_limit",
        "index_rebuild_tasks_limit",
        1,
    ),
    _field(
        "index_rebuild_settings.max_retries",
        "index_rebuild_max_retries",
        5,
    ),
    _field(
        "index_rebuild_settings.retry_base_delay",
        "index_rebuild_retry_base_delay",
        30.0,
    ),
    _field(
        "index_rebuild_settings.batch_delay",
        "index_rebuild_batch_delay",
        5.0,
    ),
    _field(
        "index_rebuild_settings.request_delay",
        "index_rebuild_request_delay",
        5.0,
    ),
    _field(
        "index_rebuild_settings.max_failure_ratio",
        "index_rebuild_max_failure_ratio",
        0.02,
    ),
    _field("recall_engine.session_cache_enabled", "session_cache_enabled", True),
    _field(
        "recall_engine.session_cache_ttl_seconds",
        "session_cache_ttl_seconds",
        10.0,
    ),
    _field("recall_engine.max_chain_hops", "recall_engine.max_chain_hops", 3),
    _field(
        "recall_engine.chain_hop_decay",
        "recall_engine.chain_hop_decay",
        0.7,
    ),
    _field(
        "recall_engine.chain_graph_expansion_enabled",
        "recall_engine.chain_graph_expansion_enabled",
        True,
    ),
    _field(
        "recall_engine.chain_topic_expansion_enabled",
        "recall_engine.chain_topic_expansion_enabled",
        True,
    ),
    _field("recall_engine.testing_effect_async", "testing_effect_async", True),
    _field("recall_engine.testing_effect_top_k", "testing_effect_top_k", 5),
    _field("reranker.enabled", "reranker.enabled", True),
    _field("reranker.strategy", "reranker.strategy", "mmr"),
    _field("reranker.llm_batch_size", "reranker.llm_batch_size", 10),
    _field(
        "reranker.cross_encoder_lambda",
        "reranker.cross_encoder_lambda",
        0.7,
    ),
    _field("reranker.mmr_lambda", "reranker.mmr_lambda", 0.7),
    _field("security.strict_mode", "security.strict_mode", False),
    _field("cost_control.mode", "cost_control.mode", "balanced"),
    _field(
        "cost_control.max_extra_llm_calls_per_turn",
        "cost_control.max_extra_llm_calls_per_turn",
        0,
    ),
    _field(
        "cost_control.allow_llm_reranker_in_passive_recall",
        "cost_control.allow_llm_reranker_in_passive_recall",
        False,
    ),
    _field(
        "cost_control.allow_llm_topic_strategy_d",
        "cost_control.allow_llm_topic_strategy_d",
        False,
    ),
    _field(
        "cost_control.max_reflection_parallel_llm_calls",
        "cost_control.max_reflection_parallel_llm_calls",
        2,
    ),
    _field(
        "cost_control.llm_reranker_min_candidates",
        "cost_control.llm_reranker_min_candidates",
        12,
    ),
    _field(
        "cost_control.llm_reranker_prompt_chars",
        "cost_control.llm_reranker_prompt_chars",
        3000,
    ),
    _field(
        "migration_settings.auto_migrate",
        "migration_settings.auto_migrate",
        True,
    ),
    _field(
        "migration_settings.create_backup",
        "migration_settings.create_backup",
        True,
    ),
    _field("user_profile.enabled", "user_profile.enabled", True),
    _field("user_profile.boost_strength", "user_profile.boost_strength", 0.15),
    _field("user_profile.tag_decay_rate", "user_profile.tag_decay_rate", 0.98),
    _field(
        "user_profile.min_tag_confidence",
        "user_profile.min_tag_confidence",
        0.1,
    ),
    _field("auto_learning.enabled", "auto_learning.enabled", True),
    _field("auto_learning.learning_rate", "auto_learning.learning_rate", 0.01),
    _field(
        "auto_learning.target_hit_rate_low",
        "auto_learning.target_hit_rate_low",
        0.3,
    ),
    _field(
        "auto_learning.target_hit_rate_high",
        "auto_learning.target_hit_rate_high",
        0.7,
    ),
    _field(
        "auto_learning.quality_ema_alpha",
        "auto_learning.quality_ema_alpha",
        0.2,
    ),
    _field("knowledge_base.enabled", "knowledge_base.enabled", True),
    _field(
        "knowledge_base.dedup_threshold",
        "knowledge_base.dedup_threshold",
        0.85,
    ),
    _field("knowledge_base.expire_days", "knowledge_base.expire_days", 365),
    _field("notes.enabled", "notes.enabled", True),
    _field("notes.auto_create_min_length", "notes.auto_create_min_length", 50),
    _field("notes.max_tags", "notes.max_tags", 10),
    _field("notes.max_versions", "notes.max_versions", 20),
    _field("continuity_tracking.enabled", "continuity_tracking.enabled", True),
    _field(
        "continuity_tracking.topic_ttl_days",
        "continuity_tracking.topic_ttl_days",
        7,
    ),
    _field(
        "continuity_tracking.max_pending_topics",
        "continuity_tracking.max_pending_topics",
        10,
    ),
    _field("relationship_tracking.enabled", "relationship_tracking.enabled", True),
    _field(
        "relationship_tracking.warmth_decay_per_day",
        "relationship_tracking.warmth_decay_per_day",
        0.005,
    ),
    _field("anomaly_detection.enabled", "anomaly_detection.enabled", True),
    _field("anomaly_detection.window_days", "anomaly_detection.window_days", 7),
    _field(
        "anomaly_detection.sigma_threshold",
        "anomaly_detection.sigma_threshold",
        3.0,
    ),
    _field("weight_learning.enabled", "weight_learning.enabled", False),
    _field("weight_learning.epsilon", "weight_learning.epsilon", 0.1),
    _field(
        "weight_learning.group_by_persona",
        "weight_learning.group_by_persona",
        True,
    ),
    _field("reconsolidation.enabled", "reconsolidation.enabled", False),
    _field(
        "reconsolidation.min_recall_count",
        "reconsolidation.min_recall_count",
        5,
    ),
    _field("export.enabled", "export.enabled", True),
    _field(
        "human_like_memory.recency_bump_enabled",
        "human_like_memory.recency_bump_enabled",
        True,
    ),
    _field(
        "human_like_memory.seasonal_recall_enabled",
        "human_like_memory.seasonal_recall_enabled",
        True,
    ),
    _field(
        "human_like_memory.emotion_scoring_mode",
        "human_like_memory.emotion_scoring_mode",
        "enhanced",
    ),
    _field(
        "human_like_memory.human_like_formatter_mode",
        "human_like_memory.human_like_formatter_mode",
        "rule",
    ),
    _field(
        "human_like_memory.type_aware_decay_enabled",
        "human_like_memory.type_aware_decay_enabled",
        True,
    ),
    _field("hybrid_scoring.score_alpha", "hybrid_scoring.score_alpha", 0.5),
    _field("hybrid_scoring.score_beta", "hybrid_scoring.score_beta", 0.25),
    _field("hybrid_scoring.score_gamma", "hybrid_scoring.score_gamma", 0.25),
    _field("hybrid_scoring.mmr_lambda", "hybrid_scoring.mmr_lambda", 0.7),
    _field("graph_memory.score_alpha", "graph_memory.score_alpha", 0.55),
    _field("graph_memory.score_beta", "graph_memory.score_beta", 0.2),
    _field("graph_memory.score_gamma", "graph_memory.score_gamma", 0.15),
    _field("graph_memory.score_delta", "graph_memory.score_delta", 0.1),
    _field(
        "graph_memory.temporal_edges_enabled",
        "graph_memory.temporal_edges_enabled",
        True,
        RuntimeConfigEffect.REBUILD,
    ),
    _field(
        "graph_memory.causal_edges_enabled",
        "graph_memory.causal_edges_enabled",
        True,
        RuntimeConfigEffect.REBUILD,
    ),
    _field("flashbulb.enabled", "flashbulb.enabled", True),
    _field(
        "flashbulb.intensity_threshold",
        "flashbulb.intensity_threshold",
        0.9,
    ),
    _field(
        "atom_classifier.negation_detection_enabled",
        "atom_classifier.negation_detection_enabled",
        True,
    ),
    _field(
        "write_reliability.repair_enabled", "write_reliability.repair_enabled", True
    ),
    _field("write_reliability.max_retries", "write_reliability.max_retries", 3),
    _field("episode_clustering.enabled", "episode_clustering.enabled", True),
    _field(
        "episode_clustering.time_window_hours",
        "episode_clustering.time_window_hours",
        24.0,
    ),
    _field(
        "episode_clustering.topic_overlap_threshold",
        "episode_clustering.topic_overlap_threshold",
        0.5,
    ),
    _field("semantic_compression.enabled", "semantic_compression.enabled", False),
    _field("semantic_compression.age_days", "semantic_compression.age_days", 60.0),
    _field(
        "semantic_compression.similarity_threshold",
        "semantic_compression.similarity_threshold",
        0.85,
    ),
    _field("persona_decay.enabled", "persona_decay.enabled", True),
    _field("persona_decay.default_modifier", "persona_decay.default_modifier", 1.0),
)


def build_engine_runtime_config(
    config_reader: ConfigReader,
    *,
    data_dir: str,
    stopwords_dir: Path,
    graph_memory_enabled: bool,
) -> dict[str, Any]:
    """构造只含显式白名单字段的 MemoryEngine 配置快照。"""

    runtime = {
        field.target_key: config_reader.get(field.source_path, field.default)
        for field in ENGINE_RUNTIME_FIELDS
    }
    configured_stopwords = config_reader.get("recall_engine.stopwords_path", "")
    runtime.update(
        {
            "data_dir": data_dir,
            "graph_memory_enabled": graph_memory_enabled,
            "recall_engine.stopwords_path": configured_stopwords or str(stopwords_dir),
        }
    )
    return runtime


__all__ = [
    "ENGINE_RUNTIME_FIELDS",
    "EngineRuntimeField",
    "RuntimeConfigEffect",
    "build_engine_runtime_config",
]
