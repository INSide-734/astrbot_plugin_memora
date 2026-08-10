"""插件更新 feature 的公开边界。"""

from .domain import DownloadedUpdate, RuntimeUpdateError, UpdateError, UpdateRelease

__all__ = [
    "DownloadedUpdate",
    "RuntimeUpdateError",
    "UpdateError",
    "UpdateRelease",
]
