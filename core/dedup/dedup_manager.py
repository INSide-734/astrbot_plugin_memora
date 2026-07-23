"""消息去重管理器 — 基于消息 ID / 内容指纹的去重缓存"""

import hashlib
import time
from typing import Any


class DedupManager:
    """消息去重缓存（惰性过期 + 溢出时逐条淘汰）"""

    def __init__(self, max_size: int = 1000, ttl: int = 300) -> None:
        """初始化有限容量和生存时间的进程内缓存。"""

        self._cache: dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl

    @staticmethod
    async def build_dedup_key(
        event: Any,
        session_id: str,
        content: str,
        sender_id_override: str | None = None,
    ) -> str | None:
        """构建去重键，fallback 可优先采用已验证的发送者覆盖值。"""
        raw_message_id = getattr(
            getattr(event, "message_obj", None), "message_id", None
        )
        if raw_message_id is not None:
            message_id = str(raw_message_id).strip()
            if message_id:
                scope_parts = [
                    part
                    for part in (
                        DedupManager._get_platform_scope(event),
                        str(session_id).strip(),
                    )
                    if part
                ]
                scope = ":".join(scope_parts)
                return f"id:{scope}:{message_id}" if scope else f"id:{message_id}"

        sender_id = (
            sender_id_override
            if sender_id_override is not None
            else event.get_sender_id() if hasattr(event, "get_sender_id") else ""
        )
        timestamp = getattr(getattr(event, "message_obj", None), "timestamp", 0)
        fingerprint = f"{session_id}|{sender_id}|{timestamp}|{content}"
        digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
        return f"fallback:{digest}"

    @staticmethod
    def _get_platform_scope(event: Any) -> str:
        """从事件窄接口或消息对象读取稳定平台作用域。"""

        for attr_name in ("get_platform_name", "get_platform"):
            getter = getattr(event, attr_name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()

        message_obj = getattr(event, "message_obj", None)
        for attr_name in ("platform", "platform_name", "adapter_type"):
            value = getattr(message_obj, attr_name, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def is_duplicate(self, dedup_key: str | None) -> bool:
        """检查消息是否已经处理过（惰性过期）"""
        if not dedup_key:
            return False
        result = dedup_key in self._cache
        if not result:
            return False
        if time.time() - self._cache[dedup_key] > self._ttl:
            del self._cache[dedup_key]
            return False
        return True

    async def mark_processed(self, dedup_key: str | None) -> None:
        """标记消息已处理（超限时淘汰最早插入的条目）"""
        if not dedup_key:
            return
        cache = self._cache
        if len(cache) >= self._max_size:
            oldest_key = min(cache.items(), key=lambda x: x[1])[0]
            del cache[oldest_key]
        cache[dedup_key] = time.time()
