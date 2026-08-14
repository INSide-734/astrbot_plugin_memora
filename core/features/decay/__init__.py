"""记忆衰减 feature 的惰性公开边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .application import DecayOperationsMixin, DecayScheduler

__all__ = ["DecayOperationsMixin", "DecayScheduler"]

_EXPORTS = {
    "DecayOperationsMixin": (".application", "DecayOperationsMixin"),
    "DecayScheduler": (".application", "DecayScheduler"),
}


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从 decay application 延迟导入。

    参数：
        name: 待解析的包级公开符号名。

    返回：
        decay application 中的真实符号对象。

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
