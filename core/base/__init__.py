"""
基础模块
包含异常、常量、配置管理等基础组件
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ..shared.constants import (
    FAKE_TOOL_CALL_ID_PREFIX as FAKE_TOOL_CALL_ID_PREFIX,
)
from ..shared.constants import (
    FAKE_TOOL_CALL_NAME as FAKE_TOOL_CALL_NAME,
)
from ..shared.constants import (
    MEMORY_INJECTION_FOOTER as MEMORY_INJECTION_FOOTER,
)
from ..shared.constants import (
    MEMORY_INJECTION_HEADER as MEMORY_INJECTION_HEADER,
)
from .exceptions import (
    ConfigurationError,
    DatabaseError,
    InitializationError,
    MemoraException,
    MemoryProcessingError,
    ProviderNotReadyError,
    RetrievalError,
    ValidationError,
)

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
    "ConfigurationError",
    "DatabaseError",
    "InitializationError",
    "MemoraException",
    "MemoryProcessingError",
    "ProviderNotReadyError",
    "RetrievalError",
    "ValidationError",
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
