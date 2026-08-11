"""尚未归入基础配置文件的正式运行时功能分支模型。"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..platform.config.feature_contributions import WriteReliabilityConfig


def _require_unit_weight_sum(values: tuple[float, ...], section: str) -> None:
    """要求一组融合权重非零且总和为一，避免运行时隐式归一化。"""

    total = sum(values)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(f"{section} 权重总和必须为 1.0")


class AnomalyDetectionConfig(BaseModel):
    """异常行为检测配置。"""

    enabled: bool = True
    window_days: int = 7
    sigma_threshold: float = 3.0


class AtomClassifierConfig(BaseModel):
    """记忆原子分类规则配置。"""

    negation_detection_enabled: bool = True


class AutoLearningConfig(BaseModel):
    """自主学习 shadow 候选开关。"""

    enabled: bool = False


class BackupSettingsConfig(BaseModel):
    """自动备份保留配置。"""

    enabled: bool = True
    keep_days: int = 7


class ContinuityTrackingConfig(BaseModel):
    """跨轮次话题连续性配置。"""

    enabled: bool = True
    topic_ttl_days: int = 7
    max_pending_topics: int = 10


class EpisodeClusteringConfig(BaseModel):
    """派生情节聚类配置。"""

    enabled: bool = True
    time_window_hours: float = 24.0
    topic_overlap_threshold: float = 0.5


class ExportConfig(BaseModel):
    """记忆导入导出能力配置。"""

    enabled: bool = True


class FlashbulbConfig(BaseModel):
    """高情绪强度记忆衰减保护配置。"""

    enabled: bool = True
    intensity_threshold: float = Field(default=0.9, ge=0.0, le=1.0)


class HumanLikeMemoryConfig(BaseModel):
    """类人召回增强和衰减配置。"""

    recency_bump_enabled: bool = True
    seasonal_recall_enabled: bool = True
    emotion_scoring_mode: Literal["enhanced", "basic", "disabled"] = "enhanced"
    human_like_formatter_mode: Literal["rule", "disabled"] = "rule"
    type_aware_decay_enabled: bool = True


class HybridScoringConfig(BaseModel):
    """文档混合检索评分与多样性配置。"""

    score_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    score_beta: float = Field(default=0.25, ge=0.0, le=1.0)
    score_gamma: float = Field(default=0.25, ge=0.0, le=1.0)
    mmr_lambda: float = Field(default=0.7, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_score_weights(self) -> "HybridScoringConfig":
        """验证相关性、重要性与新鲜度权重总和为一。"""

        _require_unit_weight_sum(
            (self.score_alpha, self.score_beta, self.score_gamma),
            "hybrid_scoring",
        )
        return self


class KnowledgeBaseConfig(BaseModel):
    """显式知识库配置。"""

    enabled: bool = True
    dedup_threshold: float = 0.85
    expire_days: int = 365


class NotesConfig(BaseModel):
    """显式记忆笔记配置。"""

    enabled: bool = True
    auto_create_min_length: int = 50
    max_tags: int = 10
    max_versions: int = 20


class PersonaDecayConfig(BaseModel):
    """人格差异化衰减配置。"""

    enabled: bool = True
    default_modifier: float = 1.0


class PromptTemplatesConfig(BaseModel):
    """私聊和群聊抽取 Prompt 覆盖配置。"""

    group_chat_template: str = ""
    private_chat_template: str = ""


class ReconsolidationConfig(BaseModel):
    """人工复核式记忆再巩固候选配置。"""

    enabled: bool = False
    min_recall_count: int = 5


class SemanticCompressionConfig(BaseModel):
    """语义摘要 Projection 候选配置。"""

    enabled: bool = False
    age_days: float = 60.0
    similarity_threshold: float = 0.85


class UserProfileConfig(BaseModel):
    """用户画像召回增强配置。"""

    enabled: bool = True
    boost_strength: float = 0.15
    tag_decay_rate: float = 0.98
    min_tag_confidence: float = 0.1


class RuntimeFeatureConfigSections(BaseModel):
    """汇总正式功能分支，供根配置模型继承。"""

    anomaly_detection: AnomalyDetectionConfig = Field(
        default_factory=AnomalyDetectionConfig
    )
    atom_classifier: AtomClassifierConfig = Field(default_factory=AtomClassifierConfig)
    auto_learning: AutoLearningConfig = Field(default_factory=AutoLearningConfig)
    backup_settings: BackupSettingsConfig = Field(default_factory=BackupSettingsConfig)
    bot_language: Literal["zh", "en", "ru"] = "zh"
    continuity_tracking: ContinuityTrackingConfig = Field(
        default_factory=ContinuityTrackingConfig
    )
    episode_clustering: EpisodeClusteringConfig = Field(
        default_factory=EpisodeClusteringConfig
    )
    export: ExportConfig = Field(default_factory=ExportConfig)
    flashbulb: FlashbulbConfig = Field(default_factory=FlashbulbConfig)
    human_like_memory: HumanLikeMemoryConfig = Field(
        default_factory=HumanLikeMemoryConfig
    )
    hybrid_scoring: HybridScoringConfig = Field(default_factory=HybridScoringConfig)
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)
    persona_decay: PersonaDecayConfig = Field(default_factory=PersonaDecayConfig)
    prompt_templates: PromptTemplatesConfig = Field(
        default_factory=PromptTemplatesConfig
    )
    reconsolidation: ReconsolidationConfig = Field(
        default_factory=ReconsolidationConfig
    )
    semantic_compression: SemanticCompressionConfig = Field(
        default_factory=SemanticCompressionConfig
    )
    user_profile: UserProfileConfig = Field(default_factory=UserProfileConfig)
    write_reliability: WriteReliabilityConfig = Field(
        default_factory=WriteReliabilityConfig
    )


__all__ = ["RuntimeFeatureConfigSections"]
