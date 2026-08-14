"""测试 message_utils.py — message truncation utilities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.recall.processors.message_utils import (
    MAX_SINGLE_MESSAGE_LENGTH,
    store_round_with_length_check,
    truncate_message_if_needed,
)


class TestTruncateMessage:
    def test_no_truncation_needed(self) -> None:
        content = "short message"
        result, truncated = truncate_message_if_needed(content)
        assert result == content
        assert truncated is False

    def test_truncation_needed(self) -> None:
        content = "x" * (MAX_SINGLE_MESSAGE_LENGTH + 100)
        result, truncated = truncate_message_if_needed(content)
        assert truncated is True
        assert len(result) < len(content)

    def test_exact_length_no_truncation(self) -> None:
        content = "x" * MAX_SINGLE_MESSAGE_LENGTH
        result, truncated = truncate_message_if_needed(content)
        assert truncated is False

    def test_empty_string(self) -> None:
        result, truncated = truncate_message_if_needed("")
        assert result == ""
        assert truncated is False

    def test_custom_max_length(self) -> None:
        content = "x" * 50
        result, truncated = truncate_message_if_needed(content, max_length=10)
        assert truncated is True
        assert len(result) < 50


class TestStoreRound:
    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        engine = MagicMock()
        engine.add_memory = AsyncMock()
        return engine

    @pytest.fixture
    def mock_user_msg(self) -> MagicMock:
        msg = MagicMock()
        msg.content = "user message"
        msg.role = "user"
        return msg

    @pytest.fixture
    def mock_assistant_msg(self) -> MagicMock:
        msg = MagicMock()
        msg.content = "assistant reply"
        msg.role = "assistant"
        return msg

    def test_store_round_success(
        self,
        mock_engine: MagicMock,
        mock_user_msg: MagicMock,
        mock_assistant_msg: MagicMock,
    ) -> None:
        success, error = asyncio.run(
            store_round_with_length_check(
                mock_engine,
                mock_user_msg,
                mock_assistant_msg,
                session_id="s1",
                persona_id="p1",
                round_index=1,
            )
        )
        assert success is True
        assert error == ""
        mock_engine.add_memory.assert_called_once()

    def test_store_round_truncates_long_messages(self, mock_engine: MagicMock) -> None:
        user_msg = MagicMock()
        user_msg.content = "x" * (MAX_SINGLE_MESSAGE_LENGTH + 500)
        user_msg.role = "user"
        assistant_msg = MagicMock()
        assistant_msg.content = "short"
        assistant_msg.role = "assistant"

        success, _ = asyncio.run(
            store_round_with_length_check(
                mock_engine,
                user_msg,
                assistant_msg,
                session_id="s1",
                persona_id="p1",
                round_index=1,
            )
        )
        assert success is True
        call_args = mock_engine.add_memory.call_args
        metadata = call_args[1]["metadata"]
        assert metadata["truncated"] is True

    def test_store_round_exceeds_double_limit(self, mock_engine: MagicMock) -> None:
        huge = "x" * (MAX_SINGLE_MESSAGE_LENGTH + 10)
        user_msg = MagicMock()
        user_msg.content = huge
        user_msg.role = "user"
        assistant_msg = MagicMock()
        assistant_msg.content = huge
        assistant_msg.role = "assistant"

        success, error = asyncio.run(
            store_round_with_length_check(
                mock_engine,
                user_msg,
                assistant_msg,
                session_id="s1",
                persona_id="p1",
                round_index=5,
            )
        )
        assert success is False
        assert "第5轮" in error
        mock_engine.add_memory.assert_not_called()

    def test_store_round_none_engine(
        self, mock_user_msg: MagicMock, mock_assistant_msg: MagicMock
    ) -> None:
        success, error = asyncio.run(
            store_round_with_length_check(
                None,
                mock_user_msg,
                mock_assistant_msg,
                session_id="s1",
                persona_id="p1",
                round_index=1,
            )
        )
        assert success is False
        assert "None" in error

    def test_store_round_add_memory_exception(
        self,
        mock_engine: MagicMock,
        mock_user_msg: MagicMock,
        mock_assistant_msg: MagicMock,
    ) -> None:
        mock_engine.add_memory = AsyncMock(side_effect=RuntimeError("db error"))
        success, error = asyncio.run(
            store_round_with_length_check(
                mock_engine,
                mock_user_msg,
                mock_assistant_msg,
                session_id="s1",
                persona_id="p1",
                round_index=1,
            )
        )
        assert success is False
        assert "存储失败" in error
