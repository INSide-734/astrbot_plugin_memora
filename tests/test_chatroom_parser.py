"""chatroom_parser.py 测试 — ChatroomContextParser。"""

from __future__ import annotations

from core.processors.chatroom_parser import ChatroomContextParser


class TestChatroomParser:
    """ChatroomContextParser: extract user message from chatroom context prompts."""

    # ---- is_chatroom_context ----

    def test_is_chatroom_context_true_for_full_format(self) -> None:
        prompt = (
            "You are now in a chatroom. The chat history is as follows:\n"
            "[User A/10:30:15]: hello\n"
            "---\n"
            "Now, a new message is coming: `\n[User ID: 123456789, Nickname: User B]\nhi there`.\n"
            "Please react to it..."
        )
        assert ChatroomContextParser.is_chatroom_context(prompt) is True

    def test_is_chatroom_context_false_for_plain_message(self) -> None:
        prompt = "Hello, how are you?"
        assert ChatroomContextParser.is_chatroom_context(prompt) is False

    def test_is_chatroom_context_false_with_only_header(self) -> None:
        prompt = "You are now in a chatroom. The chat history is as follows:\nsome history"
        assert ChatroomContextParser.is_chatroom_context(prompt) is False

    def test_is_chatroom_context_false_with_only_marker(self) -> None:
        prompt = "Now, a new message is coming: hello"
        assert ChatroomContextParser.is_chatroom_context(prompt) is False

    def test_is_chatroom_context_false_for_empty_string(self) -> None:
        assert ChatroomContextParser.is_chatroom_context("") is False

    # ---- extract_actual_message ----

    def test_extract_actual_message_with_user_id_and_nickname(self) -> None:
        prompt = (
            "You are now in a chatroom. The chat history is as follows:\n"
            "[User A/10:30:15]: msg1\n---\n"
            "Now, a new message is coming: `\n[User ID: 123456789, Nickname: User B]\nHi! How are you?`.\n"
            "Please react to it..."
        )
        result = ChatroomContextParser.extract_actual_message(prompt)
        assert result == "Hi! How are you?"

    def test_extract_actual_message_without_user_id_line(self) -> None:
        prompt = (
            "You are now in a chatroom. The chat history is as follows:\n"
            "[User A/10:30:15]: msg1\n---\n"
            "Now, a new message is coming: `hello world`.\n"
            "Please react to it..."
        )
        result = ChatroomContextParser.extract_actual_message(prompt)
        assert result == "hello world"

    def test_extract_actual_message_with_chinese_content(self) -> None:
        prompt = (
            "You are now in a chatroom. The chat history is as follows:\n"
            "---\n"
            "Now, a new message is coming: `辛苦了！希望能快点恢复。`.\n"
            "Please react to it..."
        )
        result = ChatroomContextParser.extract_actual_message(prompt)
        assert "辛苦了" in result

    def test_extract_returns_original_for_non_chatroom(self) -> None:
        prompt = "Plain user message without chatroom format"
        result = ChatroomContextParser.extract_actual_message(prompt)
        assert result == prompt

    def test_extract_returns_original_for_empty_string(self) -> None:
        result = ChatroomContextParser.extract_actual_message("")
        assert result == ""

    def test_extract_multiline_message(self) -> None:
        prompt = (
            "You are now in a chatroom. The chat history is as follows:\n"
            "---\n"
            "Now, a new message is coming: `line one\nline two\nline three`.\n"
            "Please react to it..."
        )
        result = ChatroomContextParser.extract_actual_message(prompt)
        assert "line one" in result
        assert "line two" in result
