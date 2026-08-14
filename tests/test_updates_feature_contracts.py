"""updates feature 的唯一所有权契约。"""

import subprocess
import sys

import pytest

from core.features.updates import domain as feature_domain
from core.features.updates.application import manager as feature_manager


def test_updates_package_defers_feature_layer_imports() -> None:
    """导入 updates 包边界时不得提前加载应用与领域模块。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import core.features.updates as updates; "
            "assert 'core.features.updates.application' not in sys.modules; "
            "assert 'core.features.updates.domain' not in sys.modules; "
            "print(','.join(updates.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "UpdateManager" in result.stdout.strip().split(",")


def test_updates_package_lazily_exports_feature_layers() -> None:
    """updates 包应惰性解析真实分层对象并拒绝未知属性。"""

    import core.features.updates as updates_feature

    assert updates_feature.__getattr__("UpdateManager") is feature_manager.UpdateManager
    assert updates_feature.__getattr__("UpdateRelease") is feature_domain.UpdateRelease
    with pytest.raises(AttributeError, match="missing_updates_contract"):
        updates_feature.__getattr__("missing_updates_contract")


def test_legacy_update_config_reuses_feature_domain_owner() -> None:
    """旧配置路径只能导出 updates feature 的唯一配置模型。"""

    from core.platform.config.config_validator import (
        UpdateSettings as LegacyRootUpdateSettings,
    )
    from core.platform.config.feature_config import (
        UpdateSettings as LegacyFeatureUpdateSettings,
    )

    assert LegacyRootUpdateSettings is feature_domain.UpdateSettings
    assert LegacyFeatureUpdateSettings is feature_domain.UpdateSettings
