"""ConversationStore 的只读查询与数据完整性操作。"""

import json
from typing import Any

from astrbot.api import logger

from ....shared.contracts.conversation import Message


class MessageQueryMixin:
    """ConversationStore 的只读查询与数据完整性操作。"""

    connection: Any
    _write_lock: Any

    async def get_message_count(self, session_id: str) -> int:
        """
        获取会话的消息总数

        Args:
            session_id: 会话ID

        Returns:
            int: 消息数量
        """
        if self.connection is None:
            return 0
        async with self.connection.execute(
            """
            SELECT COUNT(*) as count
            FROM messages
            WHERE session_id = ?
        """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                count_value = row["count"]
                return int(count_value) if count_value is not None else 0
            return 0

    # ==================== 高级查询 ====================

    async def get_user_message_stats(self, session_id: str) -> dict[str, int]:
        """
        获取会话中各用户的消息统计 (群聊场景)

        Args:
            session_id: 会话ID

        Returns:
            Dict[str, int]: {sender_id: message_count}
        """
        if self.connection is None:
            return {}
        async with self.connection.execute(
            """
            SELECT sender_id, COUNT(*) as count
            FROM messages
            WHERE session_id = ? AND role = 'user'
            GROUP BY sender_id
        """,
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        stats = {}
        for row in rows:
            stats[row["sender_id"]] = row["count"]

        return stats

    async def update_message_metadata(self, message_id: int, metadata: dict) -> bool:
        """
        更新消息的metadata

        Args:
            message_id: 消息ID
            metadata: 新的metadata字典

        Returns:
            bool: 是否更新成功
        """
        if self.connection is None:
            return False

        try:
            async with self._write_lock:
                await self.connection.execute(
                    """
                    UPDATE messages
                    SET metadata = ?
                    WHERE id = ?
                    """,
                    (json.dumps(metadata, ensure_ascii=False), message_id),
                )
                await self.connection.commit()
            logger.debug(f"[ConversationStore] 更新消息metadata: id={message_id}")
            return True
        except Exception as e:
            logger.error(f"更新消息metadata失败: {e}", exc_info=True)
            return False

    async def search_messages(
        self, session_id: str, keyword: str, limit: int = 20
    ) -> list[Message]:
        """
        搜索会话中包含关键词的消息

        Args:
            session_id: 会话ID
            keyword: 搜索关键词
            limit: 限制数量

        Returns:
            List[Message]: 匹配的消息列表
        """
        if self.connection is None:
            return []
        async with self.connection.execute(
            """
            SELECT id, session_id, role, content, sender_id, sender_name,
                   group_id, platform, timestamp, metadata
            FROM messages
            WHERE session_id = ? AND content LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """,
            (session_id, f"%{keyword}%", limit),
        ) as cursor:
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            messages.append(
                Message.from_dict(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "role": row["role"],
                        "content": row["content"],
                        "sender_id": row["sender_id"],
                        "sender_name": row["sender_name"],
                        "group_id": row["group_id"],
                        "platform": row["platform"],
                        "timestamp": row["timestamp"],
                        "metadata": row["metadata"],
                    }
                )
            )

        return messages

    async def get_messages_range(
        self, session_id: str, offset: int = 0, limit: int = 50
    ) -> list[Message]:
        """
        按范围获取会话消息（使用 SQL OFFSET/LIMIT）

        Args:
            session_id: 会话ID
            offset: 跳过的消息数量（从最旧的开始计算）
            limit: 获取的消息数量

        Returns:
            List[Message]: 消息列表（按时间升序）
        """
        if self.connection is None:
            return []

        # message_seq 在 schema migration 中建立；它不受 timestamp 倒流影响。
        query = """
            SELECT id, session_id, role, content, sender_id, sender_name,
                   group_id, platform, timestamp, metadata
            FROM messages
            WHERE session_id = ?
            ORDER BY message_seq ASC
            LIMIT ? OFFSET ?
        """

        async with self.connection.execute(
            query, (session_id, limit, offset)
        ) as cursor:
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            messages.append(
                Message.from_dict(
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "role": row["role"],
                        "content": row["content"],
                        "sender_id": row["sender_id"],
                        "sender_name": row["sender_name"],
                        "group_id": row["group_id"],
                        "platform": row["platform"],
                        "timestamp": row["timestamp"],
                        "metadata": row["metadata"],
                    }
                )
            )

        logger.debug(
            f"[get_messages_range] session={session_id}, offset={offset}, "
            f"limit={limit}, 实际获取={len(messages)}条"
        )

        return messages

    async def sync_message_counts(self) -> dict[str, int]:
        """
        同步所有会话的 message_count 与实际消息数量

        用于修复 message_count 不一致的问题（如删除消息后未更新计数）

        Returns:
            Dict[str, int]: {session_id: 修正后的count}
        """
        if self.connection is None:
            return {}

        fixed_sessions = {}

        try:
            async with self._write_lock:
                async with self.connection.execute(
                    """
                    SELECT s.session_id,
                           s.message_count AS recorded_count,
                           COUNT(m.id) AS actual_count
                    FROM sessions s
                    LEFT JOIN messages m ON m.session_id = s.session_id
                    GROUP BY s.session_id
                    HAVING s.message_count != COUNT(m.id)
                    """
                ) as cursor:
                    rows = await cursor.fetchall()

                for row in rows:
                    session_id = row["session_id"]
                    recorded_count = row["recorded_count"]
                    actual_count = int(row["actual_count"] or 0)
                    await self.connection.execute(
                        """
                        UPDATE sessions
                        SET message_count = ?
                        WHERE session_id = ?
                        """,
                        (actual_count, session_id),
                    )
                    fixed_sessions[session_id] = actual_count
                    logger.info(
                        f"[ConversationStore] 修复会话 message_count: "
                        f"{session_id} ({recorded_count} -> {actual_count})"
                    )

                if fixed_sessions:
                    await self.connection.commit()
                    logger.info(
                        f"[ConversationStore] 共修复 {len(fixed_sessions)} 个会话的 message_count"
                    )
                else:
                    logger.info(
                        "[ConversationStore] 所有会话的 message_count 均正确，无需修复"
                    )

            return fixed_sessions

        except Exception as e:
            logger.error(f"同步 message_count 失败: {e}", exc_info=True)
            return {}

    async def reset_summarized_index_if_needed(self, session_id: str) -> bool:
        """
        检查并重置 last_summarized_index（如果它超出实际消息范围）

        Args:
            session_id: 会话ID

        Returns:
            bool: 是否进行了重置
        """
        if self.connection is None:
            return False

        try:
            async with self._write_lock:
                # 获取会话信息
                async with self.connection.execute(
                    "SELECT metadata, message_count FROM sessions WHERE session_id = ?",
                    (session_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if not row:
                    return False

                metadata_str = row["metadata"] or "{}"
                metadata = json.loads(metadata_str)
                message_count = row["message_count"]

                last_summarized_index = metadata.get("last_summarized_index", 0)

                # 如果 last_summarized_index 超出实际消息数量，重置为0
                if last_summarized_index > message_count:
                    metadata["last_summarized_index"] = 0
                    await self.connection.execute(
                        """
                        UPDATE sessions
                        SET metadata = ?
                        WHERE session_id = ?
                        """,
                        (json.dumps(metadata, ensure_ascii=False), session_id),
                    )
                    await self.connection.commit()
                    logger.warning(
                        f"[ConversationStore] 重置 last_summarized_index: "
                        f"{session_id} ({last_summarized_index} -> 0, 实际消息数={message_count})"
                    )
                    return True

            return False

        except Exception as e:
            logger.error(f"检查 last_summarized_index 失败: {e}", exc_info=True)
            return False
