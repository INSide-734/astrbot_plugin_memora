"""对话连续性 — 追踪未完成话题，下次对话优先注入。

当会话结束时标记活跃话题，用户在后续会话中返回时，
自动将未完成话题注入上下文，帮助恢复对话连续性。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from astrbot.api import logger

_DEFAULT_TOPIC_TTL_SEC = 86400 * 7
_MAX_PENDING_TOPICS = 10
_CONTINUITY_STATE_FILE = "continuity_state.json"


class ContinuityTracker:
    """追踪会话间的话题连续性。"""

    def __init__(
        self,
        data_dir: str = "",
        topic_ttl_sec: float = _DEFAULT_TOPIC_TTL_SEC,
        max_topics: int = _MAX_PENDING_TOPICS,
    ) -> None:
        """初始化状态目录、话题 TTL 和单 session 上限。"""

        self._data_dir = data_dir
        self._topic_ttl_sec = max(3600.0, topic_ttl_sec)
        self._max_topics = max(1, min(50, max_topics))
        self._pending: dict[str, list[dict[str, Any]]] = {}

    def mark_topics(
        self,
        session_id: str,
        topics: list[str],
        importance: float = 0.5,
    ) -> None:
        """为指定 session 新增或刷新待续话题。"""

        if not session_id or not topics:
            return
        now = time.time()
        session_topics = self._pending.setdefault(session_id, [])
        for topic in topics:
            if not topic or not topic.strip():
                continue
            t = topic.strip()
            existing = next(
                (item for item in session_topics if item["topic"] == t),
                None,
            )
            if existing:
                existing["last_seen_ts"] = now
                existing["importance"] = max(
                    existing.get("importance", importance),
                    importance,
                )
            else:
                session_topics.append(
                    {
                        "topic": t,
                        "last_seen_ts": now,
                        "topic_keywords": t.lower().split(),
                        "importance": importance,
                    }
                )
        session_topics.sort(key=lambda x: x["last_seen_ts"], reverse=True)
        self._pending[session_id] = session_topics[: self._max_topics]

    def resolve_session(self, session_id: str) -> None:
        """记录一次反思窗口完成，并保留该 session 的待续话题。"""

        logger.debug(
            "[连续性追踪] 会话收尾完成，保留话题数=%s",
            len(self._pending.get(session_id, [])),
        )

    def get_pending_topics(
        self,
        session_id: str,
        max_return: int = 3,
    ) -> list[dict[str, Any]]:
        """返回指定 session 内未过期且按衰减重要性排序的话题副本。"""

        if session_id not in self._pending:
            return []
        now = time.time()
        active: list[dict[str, Any]] = []
        for item in self._pending[session_id]:
            age = now - item["last_seen_ts"]
            if age > self._topic_ttl_sec:
                continue
            decay = max(0.3, 1.0 - age / self._topic_ttl_sec)
            active.append(
                {
                    "topic": item["topic"],
                    "last_seen_ts": item["last_seen_ts"],
                    "importance": round(item.get("importance", 0.5) * decay, 4),
                    "age_hours": round(age / 3600.0, 1),
                }
            )
        active.sort(key=lambda x: x["importance"], reverse=True)
        return active[:max_return]

    def clear_session(self, session_id: str) -> None:
        """清除指定 session 的全部待续话题。"""

        self._pending.pop(session_id, None)

    def get_continuity_context(self, session_id: str) -> str | None:
        """把最多三个有效话题格式化为临时模型上下文。"""

        pending = self.get_pending_topics(session_id, max_return=3)
        if not pending:
            return None
        names = [p["topic"] for p in pending]
        if len(names) == 1:
            return f"上次聊到了「{names[0]}」，话题尚未结束。"
        elif len(names) == 2:
            return f"上次聊到了「{names[0]}」和「{names[1]}」，话题尚未结束。"
        else:
            inner = "」、「".join(names[:-1])
            return f"上次聊到了「{inner}」和「{names[-1]}」，话题尚未结束。"

    def _state_path(self) -> str:
        """返回连续性状态文件的固定路径。"""

        return os.path.join(self._data_dir, _CONTINUITY_STATE_FILE)

    def save_state(self) -> None:
        """同步保存未过期话题，并在失败时保留内存状态。"""

        if not self._data_dir:
            return
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            now = time.time()
            clean = {}
            for sid, topics in self._pending.items():
                active = [
                    t for t in topics if now - t["last_seen_ts"] <= self._topic_ttl_sec
                ]
                if active:
                    clean[sid] = active
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False)
            self._pending = clean
        except OSError:
            logger.warning("[连续性追踪] 状态保存失败")

    def load_state(self) -> None:
        """同步恢复未过期话题，并应用当前配置的单 session 上限。"""

        if not self._data_dir:
            return
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            self._pending = {}
            for sid, topics in raw.items():
                active = [
                    t
                    for t in topics
                    if now - t.get("last_seen_ts", 0) <= self._topic_ttl_sec
                ]
                if active:
                    active.sort(
                        key=lambda item: float(item.get("last_seen_ts", 0) or 0),
                        reverse=True,
                    )
                    self._pending[sid] = active[: self._max_topics]
            total = sum(len(v) for v in self._pending.values())
            logger.info(
                "[连续性追踪] 状态恢复完成，session_count=%s, topic_count=%s",
                len(self._pending),
                total,
            )
        except Exception:
            logger.warning("[连续性追踪] 状态恢复失败，使用空状态")


__all__ = ["ContinuityTracker"]
