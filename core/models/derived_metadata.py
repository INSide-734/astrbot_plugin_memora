"""派生元数据注解旧路径兼容导出。

真实实现已迁至 ``core.features.evaluation.domain.derived_metadata``；本模块只
保留单实现 re-export，供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..features.evaluation.domain.derived_metadata import *  # noqa: F401,F403
from ..features.evaluation.domain.derived_metadata import __all__ as __all__
