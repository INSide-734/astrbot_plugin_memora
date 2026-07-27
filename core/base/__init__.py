"""
基础模块
包含异常、常量、配置管理等基础组件
"""

from .config_manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigManager,
    ConfigPersistenceError,
    ConfigValidationError,
)
from .constants import (
    FAKE_TOOL_CALL_ID_PREFIX as FAKE_TOOL_CALL_ID_PREFIX,
)
from .constants import (
    FAKE_TOOL_CALL_NAME as FAKE_TOOL_CALL_NAME,
)
from .constants import (
    MEMORY_INJECTION_FOOTER as MEMORY_INJECTION_FOOTER,
)
from .constants import (
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
