"""AstrBot 配置映射与运行时控制面契约。"""

from .ownership import (
    CONFIG_SECTION_OWNERSHIP,
    ConfigOwnershipKind,
    ConfigSectionOwnership,
    resolve_config_ownership,
)
from .runtime_effects import (
    REBUILD_REQUIRED_PATHS,
    RuntimeConfigEffect,
    classify_config_effects,
)

__all__ = [
    "CONFIG_SECTION_OWNERSHIP",
    "REBUILD_REQUIRED_PATHS",
    "ConfigOwnershipKind",
    "ConfigSectionOwnership",
    "RuntimeConfigEffect",
    "classify_config_effects",
    "resolve_config_ownership",
]
