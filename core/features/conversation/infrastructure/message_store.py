"""ConversationStore 的消息存储操作。"""

import inspect
import time
from typing import Any, cast

from astrbot.api import logger

from ....shared.contracts.conversation import Message, serialize_to_json
from .message_queries import MessageQueryMixin


class MessageStoreMixin(MessageQueryMixin):
    """ConversationStore 的消息 CRUD 与查询方法。"""

    # ==================== 消息管理 ====================

    async def add_message(self, message: Message) -> int:
        """
        添加消息到数据库

        Args:
            message: 消息对象

        Returns:
            int: 消息ID
        """
        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")

        platform = message.platform or "unknown"
        if not isinstance(platform, str):
            platform = getattr(platform, "name", str(platform))
            logger.warning(
                f"[add_message] platform 参数不是字符串类型，已自动转换为: {platform}"
            )

        sender_id = message.sender_id or message.session_id
        content = Message.content_to_text(message.content)
        now = time.time()
        async with self._write_lock:
            try:
                await self.connection.execute("BEGIN IMMEDIATE")
                await self.connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id, platform, created_at, last_active_at,
                        message_count, participants, metadata
                    )
                    VALUES (?, ?, ?, ?, 0, '[]', '{}')
                    ON CONFLICT(session_id) DO NOTHING
                    """,
                    (message.session_id, platform, now, message.timestamp),
                )
                seq_cursor = await self.connection.execute(
                    """
                    SELECT COALESCE(MAX(message_seq), 0) + 1
                    FROM messages WHERE session_id = ?
                    """,
                    (message.session_id,),
                )
                seq_row = await seq_cursor.fetchone()
                message_seq = int(seq_row[0] if seq_row else 1)
                cursor = await self.connection.execute(
                    """
                    INSERT INTO messages (
                        session_id, role, content, sender_id, sender_name,
                        group_id, platform, timestamp, metadata, message_seq
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.session_id,
                        message.role,
                        content,
                        sender_id,
                        message.sender_name,
                        message.group_id,
                        platform,
                        message.timestamp,
                        serialize_to_json(message.metadata),
                        message_seq,
                    ),
                )

                message_id = cursor.lastrowid if cursor.lastrowid else 0
                await self.connection.execute(
                    """
                    UPDATE sessions
                    SET message_count = message_count + 1,
                        last_active_at = ?,
                        metadata = CASE
                            WHEN json_valid(metadata) THEN json_set(
                                metadata,
                                '$.chat_type', ?,
                                '$.group_id', ?,
                                '$.scope_id', ?
                            )
                            ELSE metadata
                        END,
                        participants = CASE
                            WHEN ? = '' THEN participants
                            WHEN EXISTS (
                                SELECT 1
                                FROM json_each(COALESCE(NULLIF(participants, ''), '[]'))
                                WHERE value = ?
                            ) THEN participants
                            ELSE json_insert(
                                COALESCE(NULLIF(participants, ''), '[]'),
                                '$[#]',
                                ?
                            )
                        END
                    WHERE session_id = ?
                    """,
                    (
                        message.timestamp,
                        "group" if message.group_id else "private",
                        message.group_id,
                        message.group_id or message.session_id,
                        sender_id,
                        sender_id,
                        sender_id,
                        message.session_id,
                    ),
                )
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise

        logger.debug(
            f"[ConversationStore] 添加消息: session={message.session_id}, role={message.role}"
        )
        return message_id

    async def get_messages(
        self, session_id: str, limit: int = 50, sender_id: str | None = None
    ) -> list[Message]:
        """
        获取会话消息 (支持按发送者过滤)

        Args:
            session_id: 会话ID
            limit: 限制数量
            sender_id: 可选,按发送者ID过滤

        Returns:
            List[Message]: 消息列表 (按时间升序)
        """
        if sender_id:
            # 按发送者过滤
            query = """
                SELECT id, session_id, role, content, sender_id, sender_name,
                       group_id, platform, timestamp, metadata
                FROM messages
                WHERE session_id = ? AND sender_id = ?
                ORDER BY message_seq DESC
                LIMIT ?
            """
            params = (session_id, sender_id, limit)
        else:
            # 获取所有消息
            query = """
                SELECT id, session_id, role, content, sender_id, sender_name,
                       group_id, platform, timestamp, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY message_seq DESC
                LIMIT ?
            """
            params = (session_id, limit)

        if self.connection is None:
            return []
        async with self.connection.execute(query, params) as cursor:
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

        # 反转列表,返回时间升序
        messages.reverse()
        return messages

    async def find_user_sender_names(
        self,
        *,
        sender_id: str,
        session_id: str | None = None,
        private_platform: str | None = None,
    ) -> set[str]:
        """读取同 user 和已证明作用域中的非空历史显示名称。"""

        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        self._identity_name_scope(
            session_id=session_id,
            private_platform=private_platform,
        )
        cursor = await self.connection.execute(
            """
            SELECT DISTINCT sender_name
            FROM messages
            WHERE role = 'user' AND sender_id = :sender_id
              AND sender_name IS NOT NULL AND sender_name <> ''
              AND (
                (:session_id IS NOT NULL AND session_id = :session_id)
                OR (
                  :private_platform IS NOT NULL
                  AND platform = :private_platform
                  AND group_id IS NULL
                )
              )
            """,
            {
                "sender_id": sender_id,
                "session_id": session_id,
                "private_platform": private_platform,
            },
        )
        rows = await cursor.fetchall()
        return {str(row[0]) for row in rows}

    async def update_user_sender_name(
        self,
        *,
        sender_id: str,
        sender_name: str,
        session_id: str | None = None,
        private_platform: str | None = None,
    ) -> set[str]:
        """只更新同 user、role=user 和已证明作用域的名称，返回受影响 session。"""

        if self.connection is None:
            raise RuntimeError("数据库连接未初始化")
        if not isinstance(sender_name, str) or not sender_name:
            raise ValueError("sender_name 必须是非空字符串")
        self._identity_name_scope(
            session_id=session_id,
            private_platform=private_platform,
        )
        params = {
            "sender_id": sender_id,
            "sender_name": sender_name,
            "session_id": session_id,
            "private_platform": private_platform,
        }
        async with self._write_lock:
            cursor = await self.connection.execute(
                """
                SELECT DISTINCT session_id
                FROM messages
                WHERE role = 'user' AND sender_id = :sender_id
                  AND (
                    (:session_id IS NOT NULL AND session_id = :session_id)
                    OR (
                      :private_platform IS NOT NULL
                      AND platform = :private_platform
                      AND group_id IS NULL
                    )
                  )
                  AND (sender_name IS NULL OR sender_name <> :sender_name)
                """,
                params,
            )
            changed_sessions = {str(row[0]) for row in await cursor.fetchall()}
            if not changed_sessions:
                return set()
            await self.connection.execute(
                """
                UPDATE messages
                SET sender_name = :sender_name
                WHERE role = 'user' AND sender_id = :sender_id
                  AND (
                    (:session_id IS NOT NULL AND session_id = :session_id)
                    OR (
                      :private_platform IS NOT NULL
                      AND platform = :private_platform
                      AND group_id IS NULL
                    )
                  )
                  AND (sender_name IS NULL OR sender_name <> :sender_name)
                """,
                params,
            )
            await self.connection.commit()
        return changed_sessions

    @staticmethod
    def _identity_name_scope(
        *,
        session_id: str | None,
        private_platform: str | None,
    ) -> tuple[str, tuple[str, ...]]:
        """生成仅含固定 SQL 片段的群聊或私聊名称作用域。"""

        if bool(session_id) == bool(private_platform):
            raise ValueError("必须且只能提供 session_id 或 private_platform")
        if session_id:
            return "AND session_id = ?", (session_id,)
        return "AND platform = ? AND group_id IS NULL", (private_platform or "",)

    async def trim_session_messages(
        self,
        session_id: str,
        delete_count: int,
    ) -> int:
        """兼容旧调用，但唯一委托 Store 原子 trim fence。"""
        if self.connection is None or delete_count <= 0:
            return 0
        trim = cast(Any, getattr(self, "trim_if_safe", None))
        get_epoch = cast(Any, getattr(self, "get_summary_epoch", None))
        if not callable(trim) or not callable(get_epoch):
            return 0
        epoch_value = get_epoch(session_id)
        if inspect.isawaitable(epoch_value):
            epoch_value = await epoch_value
        if not isinstance(epoch_value, (tuple, list)) or not epoch_value:
            return 0
        result = trim(session_id, int(epoch_value[0]), int(delete_count))
        if inspect.isawaitable(result):
            result = await result
        return max(0, int(getattr(result, "deleted_count", 0) or 0))

    async def delete_session_messages(self, session_id: str) -> int:
        """删除会话消息时只使用 epoch/source 原子 fence。"""
        if self.connection is None:
            return 0
        atomic_clear = cast(Any, getattr(self, "clear_session_atomically", None))
        if not callable(atomic_clear):
            return 0
        result = atomic_clear(session_id)
        if inspect.isawaitable(result):
            result = await result
        return max(0, int(cast(Any, result) or 0))
