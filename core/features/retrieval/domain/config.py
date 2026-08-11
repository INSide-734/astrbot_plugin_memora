"""检索 feature 的配置模型。"""

import math

from pydantic import BaseModel, Field, model_validator


def _require_unit_weight_sum(values: tuple[float, ...], section: str) -> None:
    """要求一组融合权重非零且总和为一，避免运行时隐式归一化。"""

    total = sum(values)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(f"{section} 权重总和必须为 1.0")


class FusionStrategyConfig(BaseModel):
    """结果融合策略配置"""

    rrf_k: int = Field(default=60, ge=1, le=1000, description="RRF参数k")


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


__all__ = ["FusionStrategyConfig", "HybridScoringConfig", "RerankerConfig"]
