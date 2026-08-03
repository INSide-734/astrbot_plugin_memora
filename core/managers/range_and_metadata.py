"""
范围查询与会话元数据 Mixin
提供消息范围查询和会话元数据读写能力。
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

from ..models.conversation_models import Message


class RangeAndMetadataMixin:
    """消息范围查询与会话元数据管理。"""

    async def get_messages_range(
        self, session_id: str, start_index: int = 0, end_index: int | None = None
    ) -> list[Message]:
        """
        获取指定范围的消息（用于滑动窗口总结）

        Args:
            session_id: 会话ID
            start_index: 起始消息索引（从0开始，包含）
            end_index: 结束消息索引（不包含），None表示到最后

        Returns:
            Message对象列表
        """
        # 先获取会话信息以确定消息总数
        session_info = await self.get_session_info(session_id)
        if not session_info:
            logger.warning(f"[get_messages_range] 会话 {session_id} 不存在")
            return []

        recorded_count = session_info.message_count

        # 获取实际消息数量（用于一致性检查）
        actual_count = await self.store.get_message_count(session_id)

        # 数据一致性检查：如果 sessions 表记录的 message_count 与实际不符
        if recorded_count != actual_count:
            logger.warning(
                f"[get_messages_range] [{session_id}] 数据不一致! "
                f"sessions表记录={recorded_count}, 实际消息数={actual_count}，正在同步..."
            )
            # 使用实际消息数量，并触发同步修复
            await self.store.sync_message_counts()

        total_messages = actual_count  # 使用实际消息数量

        # 确定实际需要获取的范围
        actual_end = end_index if end_index is not None else total_messages

        # 验证索引范围
        if start_index < 0:
            logger.warning(
                f"[get_messages_range] [{session_id}] 起始索引 {start_index} < 0，调整为 0"
            )
            start_index = 0

        if start_index >= total_messages:
            logger.warning(
                f"[get_messages_range] [{session_id}] 起始索引 {start_index} >= 实际消息总数 {total_messages}，返回空列表"
            )
            return []

        if actual_end > total_messages:
            logger.warning(
                f"[get_messages_range] [{session_id}] 结束索引 {actual_end} 超出范围，调整为 {total_messages}"
            )
            actual_end = total_messages

        if start_index >= actual_end:
            logger.warning(
                f"[get_messages_range] [{session_id}] 起始索引 {start_index} >= 结束索引 {actual_end}，返回空列表"
            )
            return []

        # 计算需要获取的消息数量
        needed_count = actual_end - start_index

        logger.debug(
            f"[get_messages_range] [{session_id}] 准备获取消息: "
            f"实际总数={total_messages}, 范围=[{start_index}:{actual_end}], "
            f"需要={needed_count}条"
        )

        # 使用 store 的 get_messages_range 方法（基于 OFFSET/LIMIT）
        result = await self.store.get_messages_range(
            session_id=session_id,
            offset=start_index,
            limit=needed_count,
        )

        logger.info(
            f"[get_messages_range] [{session_id}] 返回 {len(result)} 条消息 (索引 {start_index} 到 {actual_end})"
        )

        return result

    async def update_session_metadata(
        self, session_id: str, key: str, value: Any
    ) -> bool:
        """通过原子多字段入口持久化更新一个会话元数据键。

        Args:
            session_id: 统一会话标识。
            key: 要更新的元数据键。
            value: 要写入的元数据值。

        Returns:
            ``True`` 表示 SQLite ``commit`` 已成功完成；会话不存在、
            数据库连接不可用或写入失败时返回 ``False``。

        Raises:
            asyncio.CancelledError: 调用方取消写入时原样传播。

        Side Effects:
            只有数据库提交成功后才更新内存中的会话对象。普通写入失败会
            尝试回滚未提交事务，避免缓存和 SQLite 分叉。
        """
        return await self.update_session_metadata_fields(
            session_id,
            {key: value},
        )

    async def update_session_metadata_fields(
        self,
        session_id: str,
        updates: Mapping[str, Any],
    ) -> bool:
        """在一次 SQLite 提交中持久化多个会话元数据字段。

        Args:
            session_id: 统一会话标识。
            updates: 要合并到现有 metadata 的字段和值。

        Returns:
            ``True`` 表示完整字段集合已通过一次 SQLite ``commit`` 提交；
            会话不存在、数据库连接不可用、字段为空或写入失败时返回
            ``False``。

        Raises:
            asyncio.CancelledError: 调用方取消写入时原样传播。

        Side Effects:
            只有完整字段集合提交成功后才更新内存会话；普通失败会尝试
            回滚，避免游标推进与恢复状态清理出现部分提交。
        """

        if not updates:
            logger.warning(f"[{session_id}] 会话元数据更新字段为空，跳过写入")
            return False

        session = await self.store.get_session(session_id)
        if not session:
            logger.warning(
                f"[ConversationManager] 会话 {session_id} 不存在，无法更新元数据"
            )
            return False

        if self.store.connection is None:
            logger.error(
                f"[ConversationManager] 会话 {session_id} 没有可用数据库连接，"
                "元数据未持久化"
            )
            return False

        previous_metadata = dict(session.metadata or {})
        updated_metadata = dict(previous_metadata)
        updated_metadata.update(dict(updates))

        try:
            await self.store.connection.execute(
                """
                UPDATE sessions
                SET metadata = ?
                WHERE session_id = ?
            """,
                (json.dumps(updated_metadata, ensure_ascii=False), session_id),
            )
            await self.store.connection.commit()
        except asyncio.CancelledError:
            try:
                await self.store.connection.rollback()
            except Exception as rollback_error:
                logger.error(
                    f"[{session_id}] 取消元数据写入后的事务回滚失败：{rollback_error}",
                    exc_info=True,
                )
            raise
        except Exception as error:
            try:
                await self.store.connection.rollback()
            except Exception as rollback_error:
                logger.error(
                    f"[{session_id}] 元数据写入失败后的事务回滚失败：{rollback_error}",
                    exc_info=True,
                )
            logger.error(f"[{session_id}] 更新会话元数据失败：{error}", exc_info=True)
            return False

        session.metadata = updated_metadata
        logger.debug(
            f"[ConversationManager] 更新会话元数据: {session_id}, "
            f"字段={sorted(str(key) for key in updates)}"
        )
        return True

    async def get_session_metadata(
        self, session_id: str, key: str, default: Any = None
    ) -> Any:
        """
        获取会话元数据

        Args:
            session_id: 会话ID
            key: 元数据键
            default: 默认值

        Returns:
            元数据值，不存在则返回default
        """
        session = await self.store.get_session(session_id)
        if not session:
            return default

        return session.metadata.get(key, default)

    async def reset_session_metadata(self, session_id: str) -> bool:
        """持久化清空指定会话的元数据，并返回提交是否成功。

        Args:
            session_id: 统一会话标识。

        Returns:
            ``True`` 表示空 metadata 已提交；会话不存在、数据库连接不可用
            或写入失败时返回 ``False``。

        Raises:
            asyncio.CancelledError: 调用方取消写入时原样传播。

        Side Effects:
            只有数据库提交成功后才清空内存缓存；普通失败会尝试回滚未提交
            事务，避免 reset 只在缓存中生效。
        """
        session = await self.store.get_session(session_id)
        if not session:
            logger.warning(
                f"[ConversationManager] 尝试重置元数据失败，会话 {session_id} 不存在"
            )
            return False
        if self.store.connection is None:
            logger.error(f"[{session_id}] 没有可用数据库连接，元数据未重置")
            return False

        try:
            await self.store.connection.execute(
                """
                UPDATE sessions
                SET metadata = ?
                WHERE session_id = ?
            """,
                ("{}", session_id),
            )
            await self.store.connection.commit()
        except asyncio.CancelledError:
            try:
                await self.store.connection.rollback()
            except Exception as rollback_error:
                logger.error(
                    f"[{session_id}] 取消元数据重置后的事务回滚失败：{rollback_error}",
                    exc_info=True,
                )
            raise
        except Exception as error:
            try:
                await self.store.connection.rollback()
            except Exception as rollback_error:
                logger.error(
                    f"[{session_id}] 元数据重置失败后的事务回滚失败：{rollback_error}",
                    exc_info=True,
                )
            logger.error(f"[{session_id}] 重置会话元数据失败：{error}", exc_info=True)
            return False

        session.metadata = {}
        logger.info(
            f"[ConversationManager] 已重置会话 {session_id} 的元数据 (记忆总结计数器已清零)"
        )
        return True
