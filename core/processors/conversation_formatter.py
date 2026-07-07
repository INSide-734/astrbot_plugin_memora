"""对话格式化器"""

from datetime import datetime
from typing import Any

from astrbot.api import logger

from ..models.conversation_models import Message


class ConversationFormatter:
    """将 Message 列表格式化为对话文本"""

    def format_conversation(self, messages: list[Message]) -> str:
        formatted_lines = []
        for i, msg in enumerate(messages):
            logger.debug(
                f"[_format_conversation] 消息#{i}: "
                f"sender_id={msg.sender_id}, sender_name={msg.sender_name}, "
                f"role={msg.role}, group_id={msg.group_id}"
            )

            content_text = self._message_content_to_text(msg.content)
            sender_info = self._format_sender_info(msg)
            formatted_line = f"{sender_info} {content_text}".rstrip()
            formatted_lines.append(formatted_line)
            if msg.group_id:
                logger.debug(
                    f"[_format_conversation] 消息#{i} 格式化结果(群聊): {formatted_line[:100]}..."
                )
            else:
                logger.debug(
                    f"[_format_conversation] 消息#{i} 格式化结果(私聊): {sender_info[:50]}..."
                )
        return "\n".join(formatted_lines)

    @staticmethod
    def _format_sender_info(msg: Message) -> str:
        time_str = datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        display_name = msg.sender_name if msg.sender_name else msg.sender_id or "未知"
        is_bot = msg.metadata.get("is_bot_message", False) or msg.role == "assistant"
        if is_bot:
            return f"[Bot: {display_name} | ID: {msg.sender_id} | {time_str}]"
        return f"[{display_name} | ID: {msg.sender_id} | {time_str}]"

    @classmethod
    def _message_content_to_text(cls, content: Any) -> str:
        return Message.content_to_text(content)

    @classmethod
    def _message_part_to_text(cls, part: Any) -> tuple[str, bool]:
        return Message._content_part_to_text(part)
