"""对话格式化器"""

from datetime import datetime
from typing import Any

from astrbot.api import logger

from ..models.conversation_models import Message


class ConversationFormatter:
    """将 Message 列表格式化为对话文本"""

    def format_conversation(self, messages: list[Message]) -> str:
        """保留发送者和秒级时间，将消息列表格式化为普通对话文本。"""

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

    def format_conversation_with_source_refs(self, messages: list[Message]) -> str:
        """给每条消息添加匿名标签和正文字符数，供抽取结果精确引用。"""

        formatted_lines: list[str] = []
        for index, message in enumerate(messages):
            content_text = self._message_content_to_text(message.content)
            sender_info = self._format_sender_info(message)
            formatted_lines.append(
                f"[S{index} chars={len(content_text)}] "
                f"{sender_info} {content_text}".rstrip()
            )
        return "\n".join(formatted_lines)

    @staticmethod
    def _format_sender_info(msg: Message) -> str:
        """生成不改变既有 Prompt 契约的发送者显示前缀。"""

        time_str = datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        display_name = msg.sender_name if msg.sender_name else msg.sender_id or "未知"
        is_bot = msg.metadata.get("is_bot_message", False) or msg.role == "assistant"
        if is_bot:
            return f"[Bot: {display_name} | ID: {msg.sender_id} | {time_str}]"
        return f"[{display_name} | ID: {msg.sender_id} | {time_str}]"

    def format_conversation_compact(
        self,
        messages: list[Message],
        message_max_chars: int = 500,
        omit_sender_id: bool = True,
    ) -> str:
        """紧凑格式：省略 sender ID、合并连续同角色、秒级时间降为分钟。

        参数:
            messages: 消息列表。
            message_max_chars: 单条消息最大字符数，超出截断。
            omit_sender_id: 私聊下省略 sender ID 以减少 token。
        """
        formatted_lines = []
        prev_role = None
        msg_index = 0

        for i, msg in enumerate(messages):
            content_text = self._message_content_to_text(msg.content)
            if not content_text.strip():
                continue

            # 截断过长消息
            if message_max_chars > 0 and len(content_text) > message_max_chars:
                content_text = content_text[:message_max_chars] + "…"

            is_bot = (
                msg.metadata.get("is_bot_message", False) or msg.role == "assistant"
            )
            current_role = "bot" if is_bot else "user"
            display_name = msg.sender_name or msg.sender_id or "未知"
            is_group = bool(msg.group_id)

            # 时间压缩为分钟级
            time_str = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M")

            # 合并连续同角色消息
            if current_role == prev_role and not is_group:
                formatted_lines.append(content_text)
                prev_role = current_role
                continue

            # 构建紧凑行
            if is_bot:
                header = f"[Bot {time_str}]"
            elif is_group:
                header = f"[{display_name} {time_str}]"
            else:
                header = f"[{time_str}]"

            formatted_lines.append(f"{header} {content_text}")
            prev_role = current_role
            msg_index += 1

        return "\n".join(formatted_lines)

    @classmethod
    def _message_content_to_text(cls, content: Any) -> str:
        """把消息组件规范为纯文本。"""

        return Message.content_to_text(content)

    @classmethod
    def _message_part_to_text(cls, part: Any) -> tuple[str, bool]:
        """把单个消息组件规范为文本和媒体标记。"""

        return Message._content_part_to_text(part)
