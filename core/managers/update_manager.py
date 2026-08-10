"""插件更新应用服务的旧路径兼容导出。"""

from ..features.updates.application import UpdateManager
from ..features.updates.domain import DownloadedUpdate, UpdateError, UpdateRelease

__all__ = ["DownloadedUpdate", "UpdateError", "UpdateManager", "UpdateRelease"]
