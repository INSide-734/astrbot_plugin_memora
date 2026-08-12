"""协议身份运行时旧路径兼容导出。

真实实现已迁至 ``core.features.identity.application.runtime``；本模块只保留
单实现 re-export，供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..features.identity.application.runtime import ProtocolIdentityRuntime

__all__ = ["ProtocolIdentityRuntime"]
