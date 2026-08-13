"""运行时功能配置旧路径兼容导出。

真实实现已迁至 ``core.platform.config.runtime_feature_config``；本模块只保留
单实现 re-export，供尚未切换到 platform 路径的历史调用方与契约测试使用。
"""

from ..platform.config.runtime_feature_config import *  # noqa: F401,F403
from ..platform.config.runtime_feature_config import (  # noqa: F401
    AtomClassifierConfig,
    ExportConfig,
    FlashbulbConfig,
    HumanLikeMemoryConfig,
    HybridScoringConfig,
    PersonaDecayConfig,
    WriteReliabilityConfig,
)
from ..platform.config.runtime_feature_config import __all__ as __all__
