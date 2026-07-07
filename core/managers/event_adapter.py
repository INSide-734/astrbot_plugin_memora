"""
事件适配 Mixin
提供将 AstrBot 平台事件转换为内部消息格式的能力。
"""

from typing import Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ..models.conversation_models import Message
from .sender_resolver import _resolve_sender_name


class EventAdapterMixin:
    """将 AstrBot MessageEvent 转换为内部 Message 模型。"""

    async def add_message_from_event(
        self,
        event: Any,  # AstrBot MessageEvent
        role: str,
        content: str,
    ) -> Message:
        """
        从AstrBot事件添加消息(自动提取发送者信息)

        Args:
            event: AstrBot的MessageEvent对象
            role: 消息角色 ("user" 或 "assistant")
            content: 消息内容

        Returns:
            创建的Message对象
        """
        # 使用 unified_msg_origin 作为会话ID，确保多Bot场景下的唯一性
        session_id = event.unified_msg_origin

        # 提取发送者信息
        sender_id = None
        sender_name = None
        group_id = None

        # 尝试获取发送者ID
        if hasattr(event, "get_sender_id"):
            sender_id = event.get_sender_id()
        elif hasattr(event, "sender_id"):
            sender_id = event.sender_id

        # 如果还是没有sender_id,使用session_id作为后备
        if not sender_id:
            sender_id = session_id

        sender_name = _resolve_sender_name(event, sender_id)

        # Debug: 记录原始 message_obj.sender 信息
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "sender"):
            raw_sender = event.message_obj.sender
            logger.debug(
                f"[add_message_from_event] [{session_id}] 原始sender对象: "
                f"user_id={getattr(raw_sender, 'user_id', 'N/A')}, "
                f"nickname={getattr(raw_sender, 'nickname', 'N/A')}"
            )

        # 判断是否群聊（使用 get_message_type 而非 is_group，更可靠）
        is_group = False
        if hasattr(event, "get_message_type"):
            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
            if is_group:
                group_id = session_id  # 群聊时session_id即为group_id

        # 群聊中助手消息：sender_name 使用 Bot 自身昵称（如果可获取）
        is_bot_message = role == "assistant"
        if is_bot_message and is_group:
            bot_name = None
            if hasattr(event, "get_self_id"):
                bot_name = event.get_self_id()
            # 尝试从 context 获取 Bot 昵称（AstrBot 通常在 message_obj 中有 self_id）
            if hasattr(event, "message_obj") and hasattr(event.message_obj, "self_id"):
                bot_name = str(event.message_obj.self_id)
            if bot_name:
                sender_id = bot_name
                sender_name = bot_name

        # 调试日志：记录最终获取到的发送者信息
        logger.debug(
            f"[add_message_from_event] [{session_id}] 最终发送者信息: "
            f"sender_id={sender_id}, sender_name='{sender_name}', "
            f"role={role}, is_group={is_group}, group_id={group_id}"
        )

        # 获取平台名称（字符串）
        platform = (
            event.get_platform_name()
            if hasattr(event, "get_platform_name")
            else "unknown"
        )

        return await self.add_message(
            session_id=session_id,
            role=role,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            platform=platform,
            is_bot_message=(role == "assistant"),
        )
