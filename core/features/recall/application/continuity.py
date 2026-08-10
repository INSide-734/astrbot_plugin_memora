"""提供召回侧的临时连续性上下文读取服务。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger


def build_continuity_context(memory_engine: Any, session_id: str) -> str:
    """读取当前 session 的临时连续性上下文，普通失败时安全降级为空。

    Args:
        memory_engine: 持有可选 ``continuity_tracker`` 的记忆引擎。
        session_id: 当前稳定会话作用域。

    Returns:
        可注入召回上下文的连续性提示；不可用或读取失败时返回空字符串。
    """

    tracker = getattr(memory_engine, "continuity_tracker", None)
    if tracker is None or not session_id:
        return ""
    try:
        context = tracker.get_continuity_context(session_id)
    except Exception:
        logger.warning("[连续性追踪] 待续话题读取失败")
        return ""
    if not isinstance(context, str) or not context.strip():
        return ""
    return f"[连续性提示]\n{context.strip()}"


__all__ = ["build_continuity_context"]
