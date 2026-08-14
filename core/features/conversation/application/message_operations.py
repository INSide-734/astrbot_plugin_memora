"""
消息操作 Mixin
提供消息添加、上下文获取和消息查询能力。
"""

import time
from typing import Any

from ....shared.contracts.conversation import Message


class MessageOperationsMixin:
    """消息增删查操作。"""

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sender_id: str | None = None,
        sender_name: str | None = None,
        group_id: str | None = None,
        platform: str = "unknown",
        is_bot_message: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """
        添加消息到会话

        Args:
            session_id: 会话ID
            role: 角色 ("user" 或 "assistant")
            content: 消息内容
            sender_id: 发送者ID
            sender_name: 发送者昵称
            group_id: 群组ID(群聊场景)
            platform: 平台标识
            is_bot_message: 是否写入 Bot 消息标记
            metadata: 调用方提供的消息元数据，写入前复制

        Returns:
            创建的Message对象
        """
        # 如果没有sender_id,使用session_id
        if not sender_id:
            sender_id = session_id

        message_metadata = dict(metadata or {})
        if is_bot_message:
            message_metadata["is_bot_message"] = True

        # 创建消息对象
        message = Message(
            id=0,  # 将由数据库分配
            session_id=session_id,
            role=role,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            platform=platform,
            timestamp=time.time(),
            metadata=message_metadata,
        )

        # 存储到数据库
        message_id = await self.store.add_message(message)
        message.id = message_id

        # 使缓存失效(下次获取时重新加载)
        async with self._cache_lock:
            if session_id in self._cache:
                del self._cache[session_id]

        return message

    async def get_context(
        self,
        session_id: str,
        max_messages: int | None = None,
        sender_id: str | None = None,
        format_for_llm: bool = True,
    ) -> list[dict[str, str]]:
        """
        获取会话上下文(用于LLM)

        Args:
            session_id: 会话ID
            max_messages: 最大消息数(None则使用context_window_size)
            sender_id: 过滤特定发送者(群聊场景)
            format_for_llm: 是否格式化为LLM格式

        Returns:
            消息列表,格式: [{"role": "user", "content": "..."}, ...]
        """
        limit = max_messages or self.context_window_size

        # 获取消息
        messages = await self.get_messages(
            session_id=session_id, limit=limit, sender_id=sender_id, use_cache=True
        )

        if format_for_llm:
            # 格式化为LLM格式
            # 只在群聊场景(有group_id)时添加发送者名称前缀
            return [
                msg.format_for_llm(include_sender_name=bool(msg.group_id))
                for msg in messages
            ]
        else:
            # 返回原始格式
            return [msg.to_dict() for msg in messages]

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        sender_id: str | None = None,
        use_cache: bool = True,
    ) -> list[Message]:
        """
        获取会话消息

        Args:
            session_id: 会话ID
            limit: 限制数量
            sender_id: 过滤发送者
            use_cache: 是否使用缓存

        Returns:
            Message对象列表
        """
        # 如果指定了sender_id,不使用缓存(需要过滤)
        if sender_id:
            use_cache = False

        # 尝试从缓存获取
        if use_cache:
            cached_messages = await self._get_from_cache(session_id)
            if cached_messages is not None:
                # 从缓存中截取需要的数量
                return cached_messages[-limit:] if limit else cached_messages

        # 从数据库获取
        messages = await self.store.get_messages(
            session_id=session_id, limit=limit, sender_id=sender_id
        )

        # 更新缓存(仅当不是过滤查询时)
        if not sender_id and use_cache:
            await self._update_cache(session_id, messages)

        return messages
