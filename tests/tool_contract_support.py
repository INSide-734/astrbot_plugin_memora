"""Agent 工具公开 handler 契约的测试辅助。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from typing import Any, cast


async def call_text_handler(tool: Any, event: Any, **kwargs: Any) -> str:
    """调用公开工具 handler，并断言其返回可等待的文本结果。

    Args:
        tool: 待调用的 AstrBot FunctionTool 实例。
        event: AstrBot 将注入 handler 的当前消息事件替身。
        **kwargs: 模型提供且已通过 Schema 约束的工具参数。

    Returns:
        工具返回的文本结果。
    """

    handler = tool.handler
    assert callable(handler)
    pending = handler(event, **kwargs)
    assert inspect.isawaitable(pending)
    result = await cast(Awaitable[str | None], pending)
    assert isinstance(result, str)
    return result


__all__ = ["call_text_handler"]
