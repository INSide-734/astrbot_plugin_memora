"""插件代码与运行时 bundle 的资源定位。"""

from .locator import (
    PluginResourceLocator,
    ResourceNotAllowedError,
    ResourceNotFoundError,
)

__all__ = [
    "PluginResourceLocator",
    "ResourceNotAllowedError",
    "ResourceNotFoundError",
]
