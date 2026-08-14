"""平台配置 owner 契约测试。"""

import subprocess
import sys

import pytest

import core.platform.config as platform_config
from core.platform.config import manager as platform_config_manager
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


def test_platform_config_package_lazily_exports_contracts() -> None:
    """平台配置包应惰性解析公开契约，并拒绝未知属性。"""

    assert (
        platform_config.__getattr__("ConfigManager")
        is platform_config_manager.ConfigManager
    )
    with pytest.raises(AttributeError, match="missing_config_contract"):
        platform_config.__getattr__("missing_config_contract")


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


def test_platform_runtime_configs_old_path_reuses_platform_owner() -> None:
    """根配置聚合器应恒等导出 platform 拥有的运行时配置模型。"""

    from core.platform.config.config_validator import (
        IndexRebuildSettings as LegacyIndexRebuildSettings,
    )
    from core.platform.config.config_validator import (
        ProviderConfig as LegacyProviderConfig,
    )
    from core.platform.config.provider_config import ProviderConfig
    from core.platform.config.rebuild_config import IndexRebuildSettings

    assert LegacyProviderConfig is ProviderConfig
    assert LegacyIndexRebuildSettings is IndexRebuildSettings


def test_platform_transport_configs_old_paths_reuse_platform_owner() -> None:
    """宿主工具与控制台配置旧路径应恒等导出 platform 唯一模型。"""

    from core.platform.config.config_validator import (
        AgentToolsConfig as LegacyRootAgentToolsConfig,
    )
    from core.platform.config.config_validator import (
        DashboardConfig as LegacyRootDashboardConfig,
    )
    from core.platform.config.feature_config import (
        AgentToolsConfig as LegacyAgentToolsConfig,
    )
    from core.platform.config.feature_config import (
        DashboardConfig as LegacyDashboardConfig,
    )
    from core.platform.config.transport_config import AgentToolsConfig, DashboardConfig

    assert LegacyRootAgentToolsConfig is AgentToolsConfig
    assert LegacyAgentToolsConfig is AgentToolsConfig
    assert LegacyRootDashboardConfig is DashboardConfig
    assert LegacyDashboardConfig is DashboardConfig


def test_security_config_old_path_reuses_platform_owner() -> None:
    """根配置聚合器应恒等导出 platform 拥有的安全配置模型。"""

    from core.platform.config.config_validator import (
        SecurityConfig as LegacySecurityConfig,
    )
    from core.platform.config.security_config import SecurityConfig

    assert LegacySecurityConfig is SecurityConfig
