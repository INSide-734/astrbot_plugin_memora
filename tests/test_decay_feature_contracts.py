"""decay feature 与旧路径的唯一实现契约。"""

import subprocess
import sys

import pytest

import core.features.decay as decay_feature
from core.features.decay.application import operations as feature_operations


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


def test_decay_configs_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 decay feature 的配置模型。"""

    from core.features.decay.domain import ForgettingAgentConfig, ImportanceDecayConfig
    from core.platform.config.config_validator import (
        ForgettingAgentConfig as LegacyForgettingAgentConfig,
    )
    from core.platform.config.config_validator import (
        ImportanceDecayConfig as LegacyImportanceDecayConfig,
    )

    assert LegacyForgettingAgentConfig is ForgettingAgentConfig
    assert LegacyImportanceDecayConfig is ImportanceDecayConfig


def test_flashbulb_config_old_path_reuses_decay_feature_owner() -> None:
    """旧运行时配置路径应恒等导出 decay 的闪光灯记忆模型。"""

    from core.features.decay.domain import FlashbulbConfig
    from core.platform.config.runtime_feature_config import (
        FlashbulbConfig as LegacyFlashbulbConfig,
    )

    assert LegacyFlashbulbConfig is FlashbulbConfig
