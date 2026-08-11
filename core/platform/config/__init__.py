"""AstrBot 配置映射与运行时控制面契约。"""

from .manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigManager,
    ConfigPersistenceError,
    ConfigValidationError,
)
from .migrations import migrate_legacy_config
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
    "ConfigApplyResult",
    "ConfigConflictError",
    "ConfigManager",
    "ConfigOwnershipKind",
    "ConfigPersistenceError",
    "ConfigSectionOwnership",
    "ConfigValidationError",
    "migrate_legacy_config",
    "RuntimeConfigEffect",
    "classify_config_effects",
    "resolve_config_ownership",
]
