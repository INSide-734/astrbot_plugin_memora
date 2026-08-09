"""引擎运行时配置投影的旧路径兼容导出。"""

from ..platform.composition import engine_runtime_config as _implementation
from ..platform.composition.engine_runtime_config import (
    ENGINE_RUNTIME_FIELDS,
    EngineRuntimeField,
    RuntimeConfigEffect,
    build_engine_runtime_config,
)

ConfigReader = _implementation.ConfigReader

__all__ = [
    "ENGINE_RUNTIME_FIELDS",
    "EngineRuntimeField",
    "RuntimeConfigEffect",
    "build_engine_runtime_config",
]
