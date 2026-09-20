"""
会话缓存管理 Mixin
提供 ConversationManager 的 LRU 缓存管理方法。

作为 Mixin 类使用，需要宿主类在 __init__ 中设置:
- self._cache: OrderedDict[str, tuple[list[Message], int, float]]
  值为 (消息, 加载时使用的 limit, 访问时间)
- self._cache_lock: asyncio.Lock
- self.max_cache_size: int
"""

import time

from ....shared.contracts.conversation import Message


class SessionCacheMixin:
    """ConversationManager 会话的 LRU 缓存管理。"""

    async def _update_cache(self, session_id: str, messages: list[Message], limit: int):
        """
        更新LRU缓存

        Args:
            session_id: 会话ID
            messages: 消息列表
            limit: 本次加载使用的 limit，命中时用于判断缓存覆盖范围
        """
        async with self._cache_lock:
            # 如果已存在,先删除(会被添加到末尾)
            if session_id in self._cache:
                del self._cache[session_id]

            # 添加到末尾(最新)
            self._cache[session_id] = (messages, limit, time.time())

            # 如果超过容量,删除最旧的
            if len(self._cache) > self.max_cache_size:
                self._cache.popitem(last=False)  # 删除最前面的(最旧)

    async def _get_from_cache(
        self, session_id: str, limit: int
    ) -> list[Message] | None:
        """
        从缓存获取消息

        Args:
            session_id: 会话ID
            limit: 本次请求的消息数量上限，非正数表示不限量

        Returns:
            最多 limit 条消息；缓存按有限 limit 加载且已被截断（条数达到加载
            limit）时，不限量或更大的请求都返回 None，由调用方回落数据库。
        """
        async with self._cache_lock:
            entry = self._cache.get(session_id)
            if entry is None:
                return None
            messages, loaded_limit, _ = entry
            # 移到末尾(标记为最新访问)
            self._cache.move_to_end(session_id)
            # 更新访问时间
            self._cache[session_id] = (messages, loaded_limit, time.time())
            if (
                loaded_limit > 0
                and len(messages) >= loaded_limit
                and (limit <= 0 or limit > loaded_limit)
            ):
                # 有限 limit 加载且已截断时缓存只覆盖窗口尾部：不限量请求
                # （limit<=0）与更大的请求都必须回落 DB，不能静默截断。
                return None
            return messages[-limit:] if limit > 0 else messages

    async def invalidate_cache(self, session_id: str):
        """
        使指定会话的缓存失效（公共接口）

        Args:
            session_id: 会话ID
        """
        async with self._cache_lock:
            if session_id in self._cache:
                del self._cache[session_id]

    def _evict_cache(self):
        """
        LRU缓存驱逐(超过max_cache_size时)

        这个方法在_update_cache中已经处理,这里保留作为显式接口
        """
        while len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)
