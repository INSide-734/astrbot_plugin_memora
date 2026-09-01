"""
会话生命周期 Mixin
提供会话创建、查询、清理与过期管理能力。
"""

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ....shared.contracts.conversation import Session


class SessionLifecycleMixin:
    """会话的创建、查询、清除与过期清理。"""

    if TYPE_CHECKING:
        store: Any
        _cache_lock: asyncio.Lock
        _cache: Any
        session_ttl: int

        async def reset_session_metadata(self, session_id: str) -> bool: ...

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
        """清空会话前取消总结 worker，再由 Store 原子 fence epoch。

        Args:
            session_id: 统一会话标识。

        Returns:
            ``True`` 表示消息删除和元数据重置均已完成。

        Raises:
            RuntimeError: 原子清理或元数据重置失败。
        """
        atomic_clear = getattr(self.store, "clear_session_atomically", None)
        if not callable(atomic_clear):
            raise RuntimeError("summary_epoch_fence_unavailable")

        get_epoch = getattr(self.store, "get_summary_epoch", None)
        if not callable(get_epoch):
            raise RuntimeError("summary_epoch_unavailable")
        epoch_value = get_epoch(session_id)
        if inspect.isawaitable(epoch_value):
            epoch_value = await epoch_value
        if not isinstance(epoch_value, (tuple, list)) or not epoch_value:
            raise RuntimeError("summary_epoch_unavailable")
        epoch = int(epoch_value[0])

        clear_result = atomic_clear(session_id)
        if inspect.isawaitable(clear_result):
            await clear_result

        # Store 已经取消持久任务；这里再取消本地 worker，避免迟到副作用。
        scheduler = getattr(self, "summary_scheduler", None)
        cancel_jobs = getattr(scheduler, "cancel_session_jobs", None)
        if callable(cancel_jobs):
            cancelled = cancel_jobs(session_id, epoch)
            if inspect.isawaitable(cancelled):
                await cancelled
        async with self._cache_lock:
            self._cache.pop(session_id, None)
        logger.info(f"[ConversationManager] 已原子清空会话并 fence epoch: {session_id}")
        return True

    async def cleanup_expired_sessions(self) -> int:
        """清理过期会话，并在 epoch fence 后取消对应本地 worker。"""
        ttl_seconds = max(60, self.session_ttl)
        scheduler = getattr(self, "summary_scheduler", None)
        cancel_jobs = getattr(scheduler, "cancel_session_jobs", None)
        kwargs: dict[str, Any] = {"ttl_seconds": ttl_seconds}
        if callable(cancel_jobs):
            kwargs["cancel_jobs_cb"] = cancel_jobs
        deleted_count = await self.store.delete_old_sessions(**kwargs)

        # 清空缓存（可能包含已删除的会话）。
        async with self._cache_lock:
            self._cache.clear()

        if deleted_count > 0:
            logger.info(
                f"[ConversationManager] 清理过期会话: {deleted_count}个 "
                f"(TTL={ttl_seconds}秒)"
            )

        return deleted_count
