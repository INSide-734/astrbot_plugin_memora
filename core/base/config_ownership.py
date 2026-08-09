"""配置所有权注册表的旧路径兼容导出。"""

from ..platform.config.ownership import (
    CONFIG_SECTION_OWNERSHIP,
    ConfigOwnershipKind,
    ConfigSectionOwnership,
    resolve_config_ownership,
)

__all__ = [
    "CONFIG_SECTION_OWNERSHIP",
    "ConfigOwnershipKind",
    "ConfigSectionOwnership",
    "resolve_config_ownership",
]
