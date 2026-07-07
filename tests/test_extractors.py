"""core/extractors/message_content_extractor.py 测试 —
MessageContentExtractor。

conftest 将每个消息组件 Mock 为不同的 MagicMock
子类，以确保提取器中的 isinstance 检查正常工作。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

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
from core.extractors.message_content_extractor import MessageContentExtractor


# ---------------------------------------------------------------------------
# extract_message_content tests
# ---------------------------------------------------------------------------

class TestExtractMessageContent:

    # -- Plain text --

    @pytest.mark.asyncio
    async def test_plain_text_only(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Plain
        comp.text = "Hello world"
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_multiple_plain_texts_joined_with_space(self) -> None:
        event = MagicMock()
        c1 = MagicMock()
        c1.__class__ = Plain
        c1.text = "Hello"
        c2 = MagicMock()
        c2.__class__ = Plain
        c2.text = "world"
        event.get_messages.return_value = [c1, c2]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_plain_text_with_trailing_whitespace(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Plain
        comp.text = "  trimmed  "
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "trimmed"

    @pytest.mark.asyncio
    async def test_empty_plain_text_skipped(self) -> None:
        event = MagicMock()
        c1 = MagicMock()
        c1.__class__ = Plain
        c1.text = ""
        c2 = MagicMock()
        c2.__class__ = Plain
        c2.text = "valid"
        c3 = MagicMock()
        c3.__class__ = Plain
        c3.text = "   "
        event.get_messages.return_value = [c1, c2, c3]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "valid"

    # -- Image --

    @pytest.mark.asyncio
    async def test_image_without_caption(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Image
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[图片]"

    @pytest.mark.asyncio
    async def test_image_with_caption_from_request(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Image
        event.get_messages.return_value = [comp]

        req = MagicMock()
        caption_part = MagicMock()
        caption_part.text = '<image_caption>A cat sitting on a desk</image_caption>'
        req.extra_user_content_parts = [caption_part]

        result = await MessageContentExtractor.extract_message_content(event, req)
        assert result == "[图片: A cat sitting on a desk]"

    @pytest.mark.asyncio
    async def test_multiple_images_multiple_captions(self) -> None:
        event = MagicMock()
        c1 = MagicMock()
        c1.__class__ = Image
        c2 = MagicMock()
        c2.__class__ = Image
        event.get_messages.return_value = [c1, c2]

        req = MagicMock()
        part1 = MagicMock()
        part1.text = '<image_caption>First image</image_caption>'
        part2 = MagicMock()
        part2.text = '<image_caption>Second image</image_caption>'
        req.extra_user_content_parts = [part1, part2]

        result = await MessageContentExtractor.extract_message_content(event, req)
        assert result == "[图片: First image] [图片: Second image]"

    @pytest.mark.asyncio
    async def test_image_with_empty_caption_text(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Image
        event.get_messages.return_value = [comp]

        req = MagicMock()
        part = MagicMock()
        part.text = ""
        req.extra_user_content_parts = [part]

        result = await MessageContentExtractor.extract_message_content(event, req)
        assert result == "[图片]"

    @pytest.mark.asyncio
    async def test_image_caption_only_whitespace(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Image
        event.get_messages.return_value = [comp]

        req = MagicMock()
        part = MagicMock()
        part.text = "<image_caption>   </image_caption>"
        req.extra_user_content_parts = [part]

        result = await MessageContentExtractor.extract_message_content(event, req)
        assert result == "[图片]"

    @pytest.mark.asyncio
    async def test_more_images_than_captions(self) -> None:
        event = MagicMock()
        comps = []
        for _ in range(3):
            c = MagicMock()
            c.__class__ = Image
            comps.append(c)
        event.get_messages.return_value = comps

        req = MagicMock()
        part = MagicMock()
        part.text = '<image_caption>Only one</image_caption>'
        req.extra_user_content_parts = [part]

        result = await MessageContentExtractor.extract_message_content(event, req)
        assert result == "[图片: Only one] [图片] [图片]"

    # -- Other component types --

    @pytest.mark.asyncio
    async def test_record_component(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Record
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[语音]"

    @pytest.mark.asyncio
    async def test_video_component(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Video
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[视频]"

    @pytest.mark.asyncio
    async def test_file_component(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = File
        comp.name = "report.pdf"
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[文件: report.pdf]"

    @pytest.mark.asyncio
    async def test_file_component_unknown_name(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = File
        comp.name = None
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[文件: 未知文件]"

    @pytest.mark.asyncio
    async def test_face_component(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Face
        comp.id = 178
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[表情:178]"

    @pytest.mark.asyncio
    async def test_at_user(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = At
        comp.qq = "123456"
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[At:123456]"

    @pytest.mark.asyncio
    async def test_at_all(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = AtAll
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[At:全体成员]"

    @pytest.mark.asyncio
    async def test_forward_component(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Forward
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[转发消息]"

    @pytest.mark.asyncio
    async def test_reply_with_message_str(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Reply
        comp.message_str = "I agree with your point"
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[引用: I agree with your point]"

    @pytest.mark.asyncio
    async def test_reply_with_long_message_str_truncated(self) -> None:
        event = MagicMock()
        long_text = "A" * 50
        comp = MagicMock()
        comp.__class__ = Reply
        comp.message_str = long_text
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == f"[引用: {'A' * 30}]"

    @pytest.mark.asyncio
    async def test_reply_without_message_str(self) -> None:
        event = MagicMock()
        comp = MagicMock()
        comp.__class__ = Reply
        comp.message_str = None
        event.get_messages.return_value = [comp]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[引用消息]"

    @pytest.mark.asyncio
    async def test_unknown_component_skipped(self) -> None:
        event = MagicMock()
        # A real type that doesn't match any component isinstance check
        class WeirdComponent:
            pass
        unknown = WeirdComponent()
        event.get_messages.return_value = [unknown]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == ""

    @pytest.mark.asyncio
    async def test_unknown_component_with_text_field_is_preserved(self) -> None:
        event = MagicMock()

        class UnknownTextComponent:
            type = "custom_text"
            text = "platform text payload"

        event.get_messages.return_value = [UnknownTextComponent()]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "platform text payload"

    @pytest.mark.asyncio
    async def test_unknown_component_with_url_field_is_preserved_safely(self) -> None:
        event = MagicMock()

        class UnknownUrlComponent:
            type = "custom_link"
            url = "https://example.com/resource?id=1"
            secret = "must not leak"

        event.get_messages.return_value = [UnknownUrlComponent()]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "[链接: https://example.com/resource?id=1]"
        assert "must not leak" not in result

    @pytest.mark.asyncio
    async def test_mixed_components(self) -> None:
        event = MagicMock()
        p1 = MagicMock()
        p1.__class__ = Plain
        p1.text = "Look at this"
        img = MagicMock()
        img.__class__ = Image
        p2 = MagicMock()
        p2.__class__ = Plain
        p2.text = "and my cat"
        event.get_messages.return_value = [p1, img, p2]

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == "Look at this [图片] and my cat"

    @pytest.mark.asyncio
    async def test_empty_component_list(self) -> None:
        event = MagicMock()
        event.get_messages.return_value = []

        result = await MessageContentExtractor.extract_message_content(event)
        assert result == ""


# ---------------------------------------------------------------------------
# get_event_message_str tests
# ---------------------------------------------------------------------------

class TestGetEventMessageStr:

    @pytest.mark.asyncio
    async def test_uses_get_message_str_callable(self) -> None:
        event = MagicMock()
        event.get_message_str.return_value = "message from method"

        result = await MessageContentExtractor.get_event_message_str(event)
        assert result == "message from method"

    @pytest.mark.asyncio
    async def test_uses_get_message_str_async_callable(self) -> None:
        event = MagicMock()

        async def async_get_msg() -> str:
            return "async message"

        event.get_message_str = async_get_msg

        result = await MessageContentExtractor.get_event_message_str(event)
        assert result == "async message"

    @pytest.mark.asyncio
    async def test_falls_back_to_message_str_attribute(self) -> None:
        event = MagicMock(spec=[])  # No get_message_str method
        event.message_str = "  raw message with spaces  "

        result = await MessageContentExtractor.get_event_message_str(event)
        assert result == "raw message with spaces"

    @pytest.mark.asyncio
    async def test_returns_empty_string_for_non_string_message_str(self) -> None:
        event = MagicMock(spec=[])
        event.message_str = 12345  # Not a string

        result = await MessageContentExtractor.get_event_message_str(event)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_for_none_message_str(self) -> None:
        event = MagicMock(spec=[])
        event.message_str = None

        result = await MessageContentExtractor.get_event_message_str(event)
        assert result == ""
