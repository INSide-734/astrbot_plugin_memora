"""插件代码与运行时 bundle 的资源定位。"""

from .locator import (
    PluginResourceLocator,
    ResourceNotAllowedError,
    ResourceNotFoundError,
)
from .package_reader import build_package_resource_reader

__all__ = [
    "build_package_resource_reader",
    "PluginResourceLocator",
    "ResourceNotAllowedError",
    "ResourceNotFoundError",
]
