"""updates feature 与旧路径的唯一实现契约。"""

import subprocess
import sys

import pytest

from core.features.updates import domain as feature_domain
from core.features.updates.application import installer as feature_installer
from core.features.updates.application import manager as feature_manager
from core.managers import update_installer as legacy_installer
from core.managers import update_manager as legacy_manager


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


def test_legacy_update_types_reuse_feature_domain_types() -> None:
    """旧 update manager 路径只能导出 feature domain 的唯一类型。"""

    assert legacy_manager.UpdateError is feature_domain.UpdateError
    assert legacy_installer.RuntimeUpdateError is feature_domain.RuntimeUpdateError
    assert legacy_manager.UpdateRelease is feature_domain.UpdateRelease
    assert legacy_manager.DownloadedUpdate is feature_domain.DownloadedUpdate


def test_legacy_update_manager_reuses_feature_implementation() -> None:
    """旧 UpdateManager 路径只能导出 application service 的唯一实现。"""

    assert legacy_manager.UpdateManager is feature_manager.UpdateManager


def test_legacy_update_installer_reuses_feature_implementation() -> None:
    """旧 installer 路径只能导出 updates application 的唯一实现。"""

    assert legacy_installer.__all__ == feature_installer.__all__
    for name in feature_installer.__all__:
        assert getattr(legacy_installer, name) is getattr(feature_installer, name)
