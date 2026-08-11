"""配置模型与迁移期配置入口。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config_manager import (
        ConfigApplyResult,
        ConfigConflictError,
        ConfigManager,
        ConfigPersistenceError,
        ConfigValidationError,
    )

_CONFIG_MANAGER_EXPORTS = frozenset(
    {
        "ConfigApplyResult",
        "ConfigConflictError",
        "ConfigManager",
        "ConfigPersistenceError",
        "ConfigValidationError",
    }
)

__all__ = [
    "ConfigApplyResult",
    "ConfigConflictError",
    "ConfigManager",
    "ConfigPersistenceError",
    "ConfigValidationError",
]


def __getattr__(name: str) -> Any:
    """首次访问配置管理兼容符号时延迟导入旧路径 shim。"""

    if name in _CONFIG_MANAGER_EXPORTS:
        module = import_module(".config_manager", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
