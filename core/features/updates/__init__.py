"""插件更新 feature 的惰性公开边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .application import RuntimeUpdateInstaller, UpdateManager
    from .domain import DownloadedUpdate, RuntimeUpdateError, UpdateError, UpdateRelease

__all__ = [
    "DownloadedUpdate",
    "RuntimeUpdateError",
    "RuntimeUpdateInstaller",
    "UpdateError",
    "UpdateManager",
    "UpdateRelease",
]

_EXPORTS = {
    "DownloadedUpdate": (".domain", "DownloadedUpdate"),
    "RuntimeUpdateError": (".domain", "RuntimeUpdateError"),
    "RuntimeUpdateInstaller": (".application", "RuntimeUpdateInstaller"),
    "UpdateError": (".domain", "UpdateError"),
    "UpdateManager": (".application", "UpdateManager"),
    "UpdateRelease": (".domain", "UpdateRelease"),
}


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从 updates 对应分层延迟导入。

    参数：
        name: 待解析的包级公开符号名。

    返回：
        updates application 或 domain 中的真实符号对象。

    异常：
        AttributeError: 名称不属于公开 feature 边界。
    """

    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
