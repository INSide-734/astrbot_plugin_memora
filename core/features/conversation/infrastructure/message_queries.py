"""ConversationStore 的只读查询与数据完整性操作。"""

import json
from collections.abc import Sequence
from typing import Any

from astrbot.api import logger

from ....shared.contracts.conversation import Message


class MessageQueryMixin:
    """ConversationStore 的只读查询与数据完整性操作。"""

    connection: Any
    _write_lock: Any

    async def _rollback_write(self, stage: str) -> None:
        """回滚未提交的写事务；回滚自身失败只记日志，不掩盖原始异常。"""
        if self.connection is None:
            return
        try:
            await self.connection.rollback()
        except Exception:
            logger.error(f"[ConversationStore] 回滚写事务失败: {stage}", exc_info=True)

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
                try:
                    # 显式事务，且回滚必须与事务同锁：锁外回滚会回滚其他协程
                    # 刚开启的事务（例如等待写锁时被取消的调用）。
                    await self.connection.execute("BEGIN IMMEDIATE")
                    await self.connection.execute(
                        """
                        UPDATE messages
                        SET metadata = ?
                        WHERE id = ?
                        """,
                        (json.dumps(metadata, ensure_ascii=False), message_id),
                    )
                    await self.connection.commit()
                except BaseException:
                    await self._rollback_write("update_message_metadata")
                    raise
        except Exception as e:
            logger.error(f"更新消息metadata失败: {e}", exc_info=True)
            return False
        logger.debug(f"[ConversationStore] 更新消息metadata: id={message_id}")
        return True

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

    async def get_messages_seq_range(
        self,
        session_id: str,
        start_seq: int,
        end_seq: int,
        *,
        expected_count: int | None = None,
    ) -> list[Message]:
        """按不可变 message_seq 读取并验证完整来源范围。"""
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        if (
            isinstance(start_seq, bool)
            or isinstance(end_seq, bool)
            or not isinstance(start_seq, int)
            or not isinstance(end_seq, int)
            or start_seq < 0
            or end_seq <= start_seq
        ):
            raise ValueError("source_seq_range_invalid")
        expected = end_seq - start_seq
        if expected_count is not None:
            if (
                isinstance(expected_count, bool)
                or not isinstance(expected_count, int)
                or expected_count != expected
            ):
                raise ValueError("source_seq_count_invalid")
        async with self.connection.execute(
            """
            SELECT id,session_id,role,content,sender_id,sender_name,
                   group_id,platform,timestamp,metadata,message_seq
            FROM messages
            WHERE session_id=? AND message_seq>? AND message_seq<=?
            ORDER BY message_seq ASC
            """,
            (session_id, start_seq, end_seq),
        ) as cursor:
            rows = await cursor.fetchall()
        seqs = [int(row["message_seq"]) for row in rows]
        if len(rows) != expected or seqs != list(range(start_seq + 1, end_seq + 1)):
            raise ValueError("source_seq_range_incomplete")
        return [
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
            for row in rows
        ]

    async def get_message_identity_rows(
        self, message_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """按主键批量读取消息稳定身份，供只读来源对账。

        只返回主键、会话、序号、角色与正文；布尔值和小于 1 的 ID 被忽略，
        连接未初始化时显式失败，调用方据此区分「无法核对」与「消息不存在」。
        """
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        normalized = sorted(
            {
                int(message_id)
                for message_id in message_ids
                if isinstance(message_id, int)
                and not isinstance(message_id, bool)
                and message_id >= 1
            }
        )
        if not normalized:
            return {}
        rows: dict[int, dict[str, Any]] = {}
        for start in range(0, len(normalized), 200):
            chunk = tuple(normalized[start : start + 200])
            placeholders = ",".join("?" for _ in chunk)
            async with self.connection.execute(
                f"SELECT id,session_id,message_seq,role,content FROM messages "
                f"WHERE id IN ({placeholders})",
                chunk,
            ) as cursor:
                for row in await cursor.fetchall():
                    message_id = int(row["id"])
                    raw_seq = row["message_seq"]
                    rows[message_id] = {
                        "session_id": str(row["session_id"]),
                        "message_seq": (
                            int(raw_seq)
                            if isinstance(raw_seq, int)
                            and not isinstance(raw_seq, bool)
                            else None
                        ),
                        "role": str(row["role"]),
                        "content": str(row["content"]),
                    }
        return rows

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

        fixed_sessions: dict[str, int] = {}

        try:
            async with self._write_lock:
                try:
                    # 显式事务，且回滚必须与事务同锁；锁外回滚会连带回滚
                    # 其他协程在同一共享连接上刚开启的事务。
                    await self.connection.execute("BEGIN IMMEDIATE")
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

                    await self.connection.commit()
                except BaseException:
                    await self._rollback_write("sync_message_counts")
                    raise
        except Exception as e:
            logger.error(f"同步 message_count 失败: {e}", exc_info=True)
            return {}

        if fixed_sessions:
            logger.info(
                f"[ConversationStore] 共修复 {len(fixed_sessions)} 个会话的 message_count"
            )
        else:
            logger.info("[ConversationStore] 所有会话的 message_count 均正确，无需修复")
        return fixed_sessions
