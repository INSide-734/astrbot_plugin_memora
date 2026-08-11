"""decay feature 与旧路径的唯一实现契约。"""

import subprocess
import sys

import pytest

import core.features.decay as decay_feature
from core.features.decay.application import operations as feature_operations
from core.managers import decay_operations as legacy_operations
from core.schedulers import decay_scheduler as legacy_scheduler


def test_decay_package_defers_application_imports() -> None:
    """导入 decay 包边界时不得提前加载应用服务。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import core.features.decay as decay; "
            "assert 'core.features.decay.application' not in sys.modules; "
            "print(','.join(decay.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "DecayOperationsMixin,DecayScheduler"


def test_decay_package_lazily_exports_contracts() -> None:
    """decay 包应惰性解析公开契约，并拒绝未知属性。"""

    assert (
        decay_feature.__getattr__("DecayOperationsMixin")
        is feature_operations.DecayOperationsMixin
    )
    with pytest.raises(AttributeError, match="missing_decay_contract"):
        decay_feature.__getattr__("missing_decay_contract")


def test_legacy_decay_operations_reuse_feature_implementation() -> None:
    """旧 manager 路径只能导出 decay application 的唯一实现。"""

    assert legacy_operations.__all__ == feature_operations.__all__
    assert (
        legacy_operations.DecayOperationsMixin
        is feature_operations.DecayOperationsMixin
    )
    assert (
        legacy_operations._normalize_batch_metadata
        is feature_operations._normalize_batch_metadata
    )


def test_legacy_decay_scheduler_reuses_feature_implementation() -> None:
    """旧 scheduler 路径只能导出 decay application 的唯一实现。"""

    from core.features.decay.application import scheduler as feature_scheduler

    assert legacy_scheduler.__all__ == feature_scheduler.__all__
    assert legacy_scheduler.DecayScheduler is feature_scheduler.DecayScheduler


def test_decay_configs_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 decay feature 的配置模型。"""

    from core.base.config_validator import (
        ForgettingAgentConfig as LegacyForgettingAgentConfig,
    )
    from core.base.config_validator import (
        ImportanceDecayConfig as LegacyImportanceDecayConfig,
    )
    from core.features.decay.domain import ForgettingAgentConfig, ImportanceDecayConfig

    assert LegacyForgettingAgentConfig is ForgettingAgentConfig
    assert LegacyImportanceDecayConfig is ImportanceDecayConfig


def test_flashbulb_config_old_path_reuses_decay_feature_owner() -> None:
    """旧运行时配置路径应恒等导出 decay 的闪光灯记忆模型。"""

    from core.base.runtime_feature_config import (
        FlashbulbConfig as LegacyFlashbulbConfig,
    )
    from core.features.decay.domain import FlashbulbConfig

    assert LegacyFlashbulbConfig is FlashbulbConfig
