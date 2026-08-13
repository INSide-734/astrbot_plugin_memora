"""配置验证旧路径兼容导出。

真实实现已迁至 ``core.platform.config.config_validator``；本模块只保留单实现
re-export，供尚未切换到 platform 路径的历史调用方与契约测试使用。
"""

from ..platform.config.config_validator import *  # noqa: F401,F403
