"""插件更新安装服务的旧路径兼容导出。"""

from ..features.updates.application import RuntimeUpdateInstaller
from ..features.updates.domain import RuntimeUpdateError

__all__ = ["RuntimeUpdateError", "RuntimeUpdateInstaller"]
