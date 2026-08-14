"""插件更新 feature 的应用服务。"""

from .installer import RuntimeUpdateInstaller
from .manager import UpdateManager

__all__ = ["RuntimeUpdateInstaller", "UpdateManager"]
