"""
会话生命周期 Mixin
提供会话创建、查询、清理与过期管理能力。
"""

from astrbot.api import logger

from ..domain.models import Session


class SessionLifecycleMixin:
    """会话的创建、查询、清除与过期清理。"""

    async def create_or_get_session(
        self, session_id: str, platform: str = "unknown"
    ) -> Session:
        """
        创建或获取会话

        Args:
            session_id: 会话ID
            platform: 平台标识

        Returns:
            Session对象
        """
        # 尝试获取现有会话
        session = await self.store.get_session(session_id)

        if session:
            # 更新活跃时间
            await self.store.update_session_activity(session_id)
            return session

        # 创建新会话
        session = await self.store.create_session(session_id, platform)
        logger.info(f"[ConversationManager] 创建新会话: {session_id}")

        return session

    async def get_session_info(self, session_id: str) -> Session | None:
        """
        获取会话信息

        Args:
            session_id: 会话ID

        Returns:
            Session对象,不存在则返回None
        """
        session = await self.store.get_session(session_id)
        if session:
            logger.debug(
                f"[DEBUG-SessionInfo] [{session_id}] 会话信息: "
                f"message_count={session.message_count}, "
                f"created_at={session.created_at}, "
                f"last_active_at={session.last_active_at}"
            )
        else:
            logger.warning(f"[DEBUG-SessionInfo] [{session_id}] 会话不存在")
        return session

    async def get_recent_sessions(self, limit: int = 10) -> list[Session]:
        """
        获取最近活跃的会话

        Args:
            limit: 返回数量限制

        Returns:
            Session对象列表
        """
        return await self.store.get_recent_sessions(limit)

    async def clear_session(self, session_id: str) -> bool:
        """清空会话历史并持久化重置元数据。

        Args:
            session_id: 统一会话标识。

        Returns:
            ``True`` 表示消息删除和 metadata reset 请求均已完成。

        Raises:
            RuntimeError: metadata reset 未成功，无法确认会话已完整清空。
        """
        # 删除数据库中的消息
        await self.store.delete_session_messages(session_id)

        # 清除缓存
        async with self._cache_lock:
            if session_id in self._cache:
                del self._cache[session_id]
        # 同步重置会话元数据，特别是记忆总结的计数器
        metadata_reset = await self.reset_session_metadata(session_id)
        if metadata_reset is not True:
            raise RuntimeError("会话元数据重置未持久化")

        logger.info(f"[ConversationManager] 已清空会话并重置记忆上下文: {session_id}")
        return True

    async def cleanup_expired_sessions(self) -> int:
        """
        清理过期会话

        Returns:
            清理的会话数量
        """
        ttl_seconds = max(60, int(self.session_ttl))
        deleted_count = await self.store.delete_old_sessions(ttl_seconds=ttl_seconds)

        # 清空缓存(可能包含已删除的会话)
        async with self._cache_lock:
            self._cache.clear()

        if deleted_count > 0:
            logger.info(
                f"[ConversationManager] 清理过期会话: {deleted_count}个 "
                f"(TTL={ttl_seconds}秒)"
            )

        return deleted_count
