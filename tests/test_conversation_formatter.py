"""conversation_formatter.py 测试 — ConversationFormatter。"""

from __future__ import annotations

import time

import pytest

from core.features.recall.processors.conversation_formatter import ConversationFormatter
from core.shared.contracts.conversation import Message


class TestConversationFormatter:
    """ConversationFormatter: format Message lists into conversation text."""

    @pytest.fixture
    def formatter(self) -> ConversationFormatter:
        return ConversationFormatter()

    @pytest.fixture
    def sample_messages(self) -> list[Message]:
        ts = time.time()
        return [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="你好",
                sender_id="user1",
                sender_name="Alice",
                timestamp=ts,
            ),
            Message(
                id=2,
                session_id="s1",
                role="assistant",
                content="你好，有什么可以帮助你的？",
                sender_id="bot1",
                sender_name="Bot",
                timestamp=ts + 1,
                metadata={"is_bot_message": True},
            ),
        ]

    def test_format_single_message(self, formatter: ConversationFormatter) -> None:
        msg = Message(
            id=1,
            session_id="s1",
            role="user",
            content="Hello world",
            sender_id="user1",
            sender_name="Alice",
            timestamp=1000000.0,
        )
        result = formatter.format_conversation([msg])
        assert "Alice" in result
        assert "Hello world" in result

    def test_format_multiple_messages(
        self, formatter: ConversationFormatter, sample_messages: list[Message]
    ) -> None:
        result = formatter.format_conversation(sample_messages)
        assert "你好" in result
        assert "Alice" in result
        assert "Bot" in result

    def test_format_empty_list(self, formatter: ConversationFormatter) -> None:
        result = formatter.format_conversation([])
        assert result == ""

    def test_bot_message_gets_bot_prefix(
        self, formatter: ConversationFormatter
    ) -> None:
        msg = Message(
            id=1,
            session_id="s1",
            role="assistant",
            content="This is a bot reply",
            sender_id="bot1",
            sender_name="Assistant",
            timestamp=1000000.0,
        )
        result = formatter.format_conversation([msg])
        assert "[Bot:" in result

    def test_user_message_no_bot_prefix(self, formatter: ConversationFormatter) -> None:
        msg = Message(
            id=1,
            session_id="s1",
            role="user",
            content="User message",
            sender_id="user1",
            sender_name="Bob",
            timestamp=1000000.0,
        )
        result = formatter.format_conversation([msg])
        assert "[Bot:" not in result

    def test_format_falls_back_to_sender_id(
        self, formatter: ConversationFormatter
    ) -> None:
        msg = Message(
            id=1,
            session_id="s1",
            role="user",
            content="msg",
            sender_id="uid123",
            sender_name=None,
            timestamp=1000000.0,
        )
        result = formatter.format_conversation([msg])
        assert "uid123" in result

    def test_format_unknown_sender(self, formatter: ConversationFormatter) -> None:
        msg = Message(
            id=1,
            session_id="s1",
            role="user",
            content="msg",
            sender_id="",
            sender_name=None,
            timestamp=1000000.0,
        )
        result = formatter.format_conversation([msg])
        assert "未知" in result

    def test_group_chat_includes_group_id_logging(
        self, formatter: ConversationFormatter
    ) -> None:
        msg = Message(
            id=1,
            session_id="s1",
            role="user",
            content="Group message",
            sender_id="user1",
            sender_name="Alice",
            group_id="g1",
            timestamp=1000000.0,
        )
        result = formatter.format_conversation([msg])
        assert "Group message" in result
