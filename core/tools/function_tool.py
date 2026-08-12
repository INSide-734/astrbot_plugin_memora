"""AstrBot 公开 FunctionTool handler 的共享装配边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from astrbot.api import FunctionTool
from pydantic.dataclasses import dataclass


@dataclass
class AgentFunctionTool(FunctionTool):
    """把具体工具实现绑定到公开的 ``FunctionTool.handler`` 契约。"""

    def __post_init__(self) -> None:
        """在 Pydantic 完成字段初始化后绑定当前实例的异步处理器。"""

        handler = getattr(self, "_run", None)
        if not callable(handler):
            raise TypeError("Agent 工具必须实现可调用的 _run 方法")
        self.handler = cast(Callable[..., Awaitable[str | None]], handler)


__all__ = ["AgentFunctionTool"]
