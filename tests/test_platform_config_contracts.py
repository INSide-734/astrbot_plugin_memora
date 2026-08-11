"""平台配置契约的旧路径兼容测试。"""

import subprocess
import sys

import pytest

import core.base as legacy_base
from core.base import config_manager as legacy_config_manager
from core.base import config_migrations as legacy_migrations
from core.base import config_ownership as legacy_ownership
from core.base import config_runtime_effects as legacy_runtime_effects
from core.base import config_validator as legacy_validation
from core.base.atom_quality_config import (
    AtomQualityFilterConfig as LegacyAtomQualityFilterConfig,
)
from core.features.memory.domain.atom_quality_config import AtomQualityFilterConfig
from core.platform.config import manager as platform_config_manager
from core.platform.config import migrations as platform_migrations
from core.platform.config import ownership as platform_ownership
from core.platform.config import runtime_effects as platform_runtime_effects
from core.platform.config import validation as platform_validation
from core.platform.config.validation import (
    get_default_config,
    merge_config_with_defaults,
    validate_config,
    validate_runtime_config_changes,
)


def test_platform_config_manager_supports_owner_first_import() -> None:
    """全新解释器应能在未加载 base 兼容包前直接导入新 owner。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.platform.config import ConfigManager; "
            "print(ConfigManager.__module__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "core.platform.config.manager"


def test_platform_config_validation_supports_owner_first_import() -> None:
    """全新解释器应能在未加载旧验证模块前直接导入平台配置校验。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.platform.config import get_default_config; "
            "print(get_default_config.__module__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "core.platform.config.validation"


def test_atom_quality_config_old_path_reuses_memory_feature_owner() -> None:
    """旧配置路径只能恒等导出 memory feature 的唯一模型实现。"""

    assert LegacyAtomQualityFilterConfig is AtomQualityFilterConfig


def test_base_package_lazily_exports_config_manager_contracts() -> None:
    """base 包应惰性解析配置管理兼容符号，并拒绝未知属性。"""

    assert (
        legacy_base.__getattr__("ConfigManager")
        is platform_config_manager.ConfigManager
    )
    with pytest.raises(AttributeError, match="missing_config_contract"):
        legacy_base.__getattr__("missing_config_contract")


def test_runtime_effects_old_path_reuses_platform_exports() -> None:
    """旧影响分类路径只能导出 platform config 的唯一实现。"""

    assert legacy_runtime_effects.__all__ == platform_runtime_effects.__all__
    for name in platform_runtime_effects.__all__:
        assert getattr(legacy_runtime_effects, name) is getattr(
            platform_runtime_effects,
            name,
        )


def test_config_ownership_old_path_reuses_platform_exports() -> None:
    """旧 owner 注册表路径只能导出 platform config 的唯一实现。"""

    assert legacy_ownership.__all__ == platform_ownership.__all__
    for name in platform_ownership.__all__:
        assert getattr(legacy_ownership, name) is getattr(
            platform_ownership,
            name,
        )


def test_config_migrations_old_path_reuses_platform_exports() -> None:
    """旧配置迁移路径只能导出 platform config 的唯一实现。"""

    assert legacy_migrations.__all__ == platform_migrations.__all__
    for name in platform_migrations.__all__:
        assert getattr(legacy_migrations, name) is getattr(platform_migrations, name)


def test_config_manager_old_path_reuses_platform_exports() -> None:
    """旧配置管理路径只能导出 platform config 的唯一实现。"""

    assert legacy_config_manager.__all__ == platform_config_manager.__all__
    for name in platform_config_manager.__all__:
        assert getattr(legacy_config_manager, name) is getattr(
            platform_config_manager,
            name,
        )


def test_config_validation_old_path_reuses_platform_exports() -> None:
    """旧配置校验入口应恒等导出平台层的唯一编排实现。"""

    for name in platform_validation.__all__:
        assert getattr(legacy_validation, name) is getattr(platform_validation, name)


def test_platform_config_validation_builds_and_validates_defaults() -> None:
    """平台校验 owner 应生成完整默认树并返回规范化模型。"""

    defaults = get_default_config()
    config = validate_config({"recall_engine": {"top_k": 0}})

    assert defaults["session_manager"]["max_sessions"] == 100
    assert config.recall_engine.top_k == 0


def test_platform_config_validation_merges_and_checks_runtime_changes() -> None:
    """平台校验 owner 应保留默认值并校验点号路径运行时变更。"""

    merged = merge_config_with_defaults({"recall_engine": {"top_k": 7}})
    config = validate_config(merged)

    assert merged["recall_engine"]["max_k"] == 10
    assert validate_runtime_config_changes(
        config,
        {"recall_engine.top_k": 0},
    )
    assert not validate_runtime_config_changes(
        config,
        {"recall_engine.top_k": -1},
    )
