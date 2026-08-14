"""AstrBot 配置映射与运行时控制面的惰性公开契约。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
        GATE_HOT_RELOAD_PATHS,
        REBUILD_REQUIRED_PATHS,
        RuntimeConfigEffect,
        classify_config_effects,
        gate_hot_reload_required,
    )
    from .validation import (
        get_default_config,
        merge_config_with_defaults,
        validate_config,
        validate_runtime_config_changes,
    )

__all__ = [
    "CONFIG_SECTION_OWNERSHIP",
    "GATE_HOT_RELOAD_PATHS",
    "REBUILD_REQUIRED_PATHS",
    "ConfigApplyResult",
    "ConfigConflictError",
    "ConfigManager",
    "ConfigOwnershipKind",
    "ConfigPersistenceError",
    "ConfigSectionOwnership",
    "ConfigValidationError",
    "get_default_config",
    "gate_hot_reload_required",
    "merge_config_with_defaults",
    "migrate_legacy_config",
    "RuntimeConfigEffect",
    "classify_config_effects",
    "resolve_config_ownership",
    "validate_config",
    "validate_runtime_config_changes",
]

_EXPORTS = {
    "CONFIG_SECTION_OWNERSHIP": (".ownership", "CONFIG_SECTION_OWNERSHIP"),
    "GATE_HOT_RELOAD_PATHS": (".runtime_effects", "GATE_HOT_RELOAD_PATHS"),
    "REBUILD_REQUIRED_PATHS": (".runtime_effects", "REBUILD_REQUIRED_PATHS"),
    "ConfigApplyResult": (".manager", "ConfigApplyResult"),
    "ConfigConflictError": (".manager", "ConfigConflictError"),
    "ConfigManager": (".manager", "ConfigManager"),
    "ConfigOwnershipKind": (".ownership", "ConfigOwnershipKind"),
    "ConfigPersistenceError": (".manager", "ConfigPersistenceError"),
    "ConfigSectionOwnership": (".ownership", "ConfigSectionOwnership"),
    "ConfigValidationError": (".manager", "ConfigValidationError"),
    "RuntimeConfigEffect": (".runtime_effects", "RuntimeConfigEffect"),
    "classify_config_effects": (".runtime_effects", "classify_config_effects"),
    "get_default_config": (".validation", "get_default_config"),
    "gate_hot_reload_required": (
        ".runtime_effects",
        "gate_hot_reload_required",
    ),
    "merge_config_with_defaults": (".validation", "merge_config_with_defaults"),
    "migrate_legacy_config": (".migrations", "migrate_legacy_config"),
    "resolve_config_ownership": (".ownership", "resolve_config_ownership"),
    "validate_config": (".validation", "validate_config"),
    "validate_runtime_config_changes": (
        ".validation",
        "validate_runtime_config_changes",
    ),
}


def __getattr__(name: str) -> Any:
    """首次访问公开配置契约时从真实 owner 延迟导入。

    参数：
        name: 待解析的包级公开符号名。

    返回：
        真实 owner 模块中的符号对象。

    异常：
        AttributeError: 名称不属于公开配置边界。
    """

    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
