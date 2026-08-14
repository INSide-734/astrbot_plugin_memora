"""recall feature 的包边界契约。"""

import subprocess
import sys

import pytest


def test_recall_package_defers_application_imports() -> None:
    """导入 recall 包边界时不得提前加载应用服务。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import core.features.recall as recall; "
            "assert 'core.features.recall.application' not in sys.modules; "
            "print(','.join(recall.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "build_continuity_context"


def test_recall_package_lazily_exports_application_contract() -> None:
    """recall 包应惰性解析真实应用对象并拒绝未知属性。"""

    import core.features.recall as recall_feature
    from core.features.recall.application import continuity

    assert (
        recall_feature.__getattr__("build_continuity_context")
        is continuity.build_continuity_context
    )
    with pytest.raises(AttributeError, match="missing_recall_contract"):
        recall_feature.__getattr__("missing_recall_contract")


def test_recall_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 recall feature 的配置模型与类型。"""

    from core.features.recall.domain import PresetName, RecallEngineConfig
    from core.platform.config.config_validator import PresetName as LegacyPresetName
    from core.platform.config.config_validator import (
        RecallEngineConfig as LegacyRecallEngineConfig,
    )

    assert LegacyPresetName is PresetName
    assert LegacyRecallEngineConfig is RecallEngineConfig


def test_recall_filtering_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 recall feature 的过滤配置模型。"""

    from core.features.recall.domain import FilteringConfig
    from core.platform.config.config_validator import (
        FilteringConfig as LegacyFilteringConfig,
    )

    assert LegacyFilteringConfig is FilteringConfig


def test_human_like_memory_config_old_path_reuses_recall_feature_owner() -> None:
    """旧运行时路径应恒等导出 recall 的类人召回配置模型。"""

    from core.features.recall.domain import HumanLikeMemoryConfig
    from core.platform.config.runtime_feature_config import (
        HumanLikeMemoryConfig as LegacyHumanLikeMemoryConfig,
    )

    assert LegacyHumanLikeMemoryConfig is HumanLikeMemoryConfig
