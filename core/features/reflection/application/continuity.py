"""提供反思写入后的连续性话题标记与窗口收尾服务。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger


def record_continuity_topics(
    memory_engine: Any,
    session_id: str,
    memory: dict[str, Any],
) -> None:
    """把一条已成功写入 canonical 的结构化 topics 标记为待续话题。

    Args:
        memory_engine: 持有可选 ``continuity_tracker`` 的记忆引擎。
        session_id: 当前稳定会话作用域。
        memory: 已通过质量门并完成 canonical 写入的结构化记忆。
    """

    tracker = getattr(memory_engine, "continuity_tracker", None)
    if tracker is None or not session_id or not isinstance(memory, dict):
        return
    metadata = memory.get("metadata")
    topics = metadata.get("topics") if isinstance(metadata, dict) else None
    if not isinstance(topics, (list, tuple)):
        return
    normalized = list(
        dict.fromkeys(
            topic.strip()
            for topic in topics
            if isinstance(topic, str) and topic.strip()
        )
    )
    if not normalized:
        return
    try:
        importance = min(1.0, max(0.0, float(memory.get("importance", 0.5))))
    except (TypeError, ValueError):
        importance = 0.5
    try:
        tracker.mark_topics(session_id, normalized, importance=importance)
    except Exception:
        logger.warning("[连续性追踪] canonical 写后话题标记失败")


def resolve_continuity_session(memory_engine: Any, session_id: str) -> None:
    """在反思窗口完成后通知 Tracker 保留当前 session 的待续话题。

    Args:
        memory_engine: 持有可选 ``continuity_tracker`` 的记忆引擎。
        session_id: 当前稳定会话作用域。
    """

    tracker = getattr(memory_engine, "continuity_tracker", None)
    if tracker is None or not session_id:
        return
    try:
        tracker.resolve_session(session_id)
    except Exception:
        logger.warning("[连续性追踪] 会话收尾失败")


__all__ = ["record_continuity_topics", "resolve_continuity_session"]
