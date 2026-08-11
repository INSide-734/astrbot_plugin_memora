"""平台配置管理器的旧路径兼容导出。"""

from ..platform.config.manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigManager,
    ConfigPersistenceError,
    ConfigValidationError,
)

__all__ = [
    "ConfigApplyResult",
    "ConfigConflictError",
    "ConfigManager",
    "ConfigPersistenceError",
    "ConfigValidationError",
]
