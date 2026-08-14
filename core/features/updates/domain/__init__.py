"""插件更新 feature 的领域契约。"""

from .config import UpdateSettings
from .errors import RuntimeUpdateError, UpdateError
from .models import DownloadedUpdate, UpdateRelease

__all__ = [
    "DownloadedUpdate",
    "RuntimeUpdateError",
    "UpdateError",
    "UpdateRelease",
    "UpdateSettings",
]
