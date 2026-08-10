"""插件更新 feature 的公开边界。"""

from .application import UpdateManager
from .domain import DownloadedUpdate, RuntimeUpdateError, UpdateError, UpdateRelease

__all__ = [
    "DownloadedUpdate",
    "RuntimeUpdateError",
    "UpdateError",
    "UpdateManager",
    "UpdateRelease",
]
