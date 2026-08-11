"""平台提示词保护端口的关闭生命周期。"""

from __future__ import annotations

import inspect


async def close_prompt_protection(initializer: object) -> None:
    """关闭组合根发布的提示词保护端口并清理运行时作用域。

    参数:
        initializer: 持有平台提示词保护端口的组合根。
    """

    protection = getattr(initializer, "prompt_protection", None)
    if protection is None:
        return
    try:
        closer = getattr(protection, "close", None)
        if callable(closer):
            result = closer()
            if inspect.isawaitable(result):
                await result
    finally:
        if getattr(initializer, "prompt_protection", None) is protection:
            setattr(initializer, "prompt_protection", None)


__all__ = ["close_prompt_protection"]
