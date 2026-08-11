"""retrieval feature 的包边界契约。"""

import pytest


def test_retrieval_configs_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 retrieval feature 的配置模型。"""

    from core.base.config_validator import (
        FusionStrategyConfig as LegacyFusionStrategyConfig,
    )
    from core.base.config_validator import RerankerConfig as LegacyRerankerConfig
    from core.features.retrieval.domain import FusionStrategyConfig, RerankerConfig

    assert LegacyFusionStrategyConfig is FusionStrategyConfig
    assert LegacyRerankerConfig is RerankerConfig


def test_hybrid_scoring_config_old_path_reuses_retrieval_feature_owner() -> None:
    """旧运行时配置路径应恒等导出 retrieval 的混合评分模型。"""

    from core.base.runtime_feature_config import (
        HybridScoringConfig as LegacyHybridScoringConfig,
    )
    from core.features.retrieval.domain import HybridScoringConfig

    assert LegacyHybridScoringConfig is HybridScoringConfig
    assert HybridScoringConfig().model_dump() == {
        "score_alpha": 0.5,
        "score_beta": 0.25,
        "score_gamma": 0.25,
        "mmr_lambda": 0.7,
    }
    with pytest.raises(ValueError, match="hybrid_scoring 权重总和必须为 1.0"):
        HybridScoringConfig(score_alpha=0.6)
