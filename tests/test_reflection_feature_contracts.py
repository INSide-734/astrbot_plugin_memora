"""reflection feature 的包边界契约。"""

import subprocess
import sys

import pytest


def test_reflection_package_defers_feature_layer_imports() -> None:
    """导入 reflection 包边界时不得提前加载 feature 分层实现。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import core.features.reflection as reflection; "
            "assert 'core.features.reflection.application' not in sys.modules; "
            "assert 'core.features.reflection.domain' not in sys.modules; "
            "print(','.join(reflection.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "TopicBatchPreparer" in result.stdout.strip().split(",")


def test_reflection_package_lazily_exports_feature_layers() -> None:
    """包级公开名称应惰性解析到真实分层对象并拒绝未知属性。"""

    import core.features.reflection as reflection_feature
    from core.features.reflection.application import candidate_writer
    from core.features.reflection.domain import storage_outcomes

    assert (
        reflection_feature.__getattr__("build_reflection_idempotency_key")
        is candidate_writer.build_reflection_idempotency_key
    )
    assert (
        reflection_feature.__getattr__("ReflectionStoreOutcome")
        is storage_outcomes.ReflectionStoreOutcome
    )
    with pytest.raises(AttributeError, match="missing_reflection_contract"):
        reflection_feature.__getattr__("missing_reflection_contract")


def test_reflection_configs_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 reflection feature 的配置模型。"""

    from core.base.config_validator import (
        LegacyBackfillConfig as LegacyLegacyBackfillConfig,
    )
    from core.base.config_validator import (
        ReflectionEngineConfig as LegacyReflectionEngineConfig,
    )
    from core.base.config_validator import StrategyBConfig as LegacyStrategyBConfig
    from core.base.config_validator import StrategyCConfig as LegacyStrategyCConfig
    from core.base.config_validator import StrategyDConfig as LegacyStrategyDConfig
    from core.base.config_validator import (
        TopicSegmentationConfig as LegacyTopicSegmentationConfig,
    )
    from core.features.reflection.domain import (
        LegacyBackfillConfig,
        ReflectionEngineConfig,
        StrategyBConfig,
        StrategyCConfig,
        StrategyDConfig,
        TopicSegmentationConfig,
    )

    assert LegacyReflectionEngineConfig is ReflectionEngineConfig
    assert LegacyStrategyBConfig is StrategyBConfig
    assert LegacyStrategyCConfig is StrategyCConfig
    assert LegacyStrategyDConfig is StrategyDConfig
    assert LegacyLegacyBackfillConfig is LegacyBackfillConfig
    assert LegacyTopicSegmentationConfig is TopicSegmentationConfig
