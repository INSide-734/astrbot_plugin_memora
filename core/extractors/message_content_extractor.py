"""消息内容提取器 — 从 AstrBot 事件中提取文本内容"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import ProviderRequest


class MessageContentExtractor:
    """从 AstrBot 消息事件中提取标准化文本内容"""

    @staticmethod
    async def extract_message_content(
        event: AstrMessageEvent,
        req: ProviderRequest | None = None,
    ) -> str:
        """提取消息内容，按组件原始顺序拼接。"""
        from astrbot.core.message.components import (
            At,
            AtAll,
            Face,
            File,
            Forward,
            Image,
            Plain,
            Record,
            Reply,
            Video,
        )

        caption_queue: list[str] = []
        if req is not None:
            for part in getattr(req, "extra_user_content_parts", []):
                text = getattr(part, "text", "")
                if not text:
                    continue
                for m in re.findall(
                    r"<image_caption>(.*?)</image_caption>",
                    text,
                    re.DOTALL,
                ):
                    m = m.strip()
                    if m:
                        caption_queue.append(m)

        parts: list[str] = []
        caption_idx = 0

        for component in event.get_messages():
            if isinstance(component, Plain):
                text = component.text.strip() if component.text else ""
                if text:
                    parts.append(text)
            elif isinstance(component, Image):
                if caption_idx < len(caption_queue):
                    parts.append(f"[图片: {caption_queue[caption_idx]}]")
                    caption_idx += 1
                else:
                    parts.append("[图片]")
            elif isinstance(component, Record):
                parts.append("[语音]")
            elif isinstance(component, Video):
                parts.append("[视频]")
            elif isinstance(component, File):
                file_name = component.name or "未知文件"
                parts.append(f"[文件: {file_name}]")
            elif isinstance(component, Face):
                parts.append(f"[表情:{component.id}]")
            elif isinstance(component, At):
                if isinstance(component, AtAll):
                    parts.append("[At:全体成员]")
                else:
                    parts.append(f"[At:{component.qq}]")
            elif isinstance(component, Forward):
                parts.append("[转发消息]")
            elif isinstance(component, Reply):
                if component.message_str:
                    parts.append(f"[引用: {component.message_str[:30]}]")
                else:
                    parts.append("[引用消息]")
            else:
                unknown_text = MessageContentExtractor._safe_unknown_component_text(
                    component
                )
                if unknown_text:
                    parts.append(unknown_text)
                    continue
                component_type = getattr(
                    component,
                    "type",
                    component.__class__.__name__,
                )
                logger.debug(f"跳过未知消息组件: {component_type}")

        return " ".join(parts).strip()

    @staticmethod
    def _safe_unknown_component_text(component: object) -> str:
        """从未知组件中提取白名单字段，避免泄露完整对象内容。"""
        for field_name in ("text", "content", "message", "name", "url"):
            if not hasattr(component, field_name):
                continue
            value = getattr(component, field_name, None)
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text:
                continue
            text = text[:300]
            if field_name == "url":
                return f"[链接: {text}]"
            return text
        return ""

    @staticmethod
    async def get_event_message_str(event: AstrMessageEvent) -> str:
        """获取标准化的原始消息文本。"""
        get_message_str = getattr(event, "get_message_str", None)
        raw_message = ""

        if callable(get_message_str):
            raw_message = get_message_str()
            if asyncio.iscoroutine(raw_message):
                raw_message = await raw_message
        else:
            raw_message = getattr(event, "message_str", "")

        if not isinstance(raw_message, str):
            return ""

        return raw_message.strip()
