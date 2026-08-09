"""运行时配置影响分类的旧路径兼容导出。"""

from ..platform.config.runtime_effects import (
    REBUILD_REQUIRED_PATHS,
    RuntimeConfigEffect,
    classify_config_effects,
)

__all__ = [
    "REBUILD_REQUIRED_PATHS",
    "RuntimeConfigEffect",
    "classify_config_effects",
]
