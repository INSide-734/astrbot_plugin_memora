"""retrieval feature 的包边界契约。"""


def test_retrieval_configs_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 retrieval feature 的配置模型。"""

    from core.base.config_validator import (
        FusionStrategyConfig as LegacyFusionStrategyConfig,
    )
    from core.base.config_validator import RerankerConfig as LegacyRerankerConfig
    from core.features.retrieval.domain import FusionStrategyConfig, RerankerConfig

    assert LegacyFusionStrategyConfig is FusionStrategyConfig
    assert LegacyRerankerConfig is RerankerConfig
