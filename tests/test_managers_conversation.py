"""ConversationManager 及其工厂函数测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.conversation.application.conversation_manager import (
    ConversationManager,
)
from core.features.conversation.infrastructure.conversation_manager_factory import (
    create_conversation_manager,
)


class TestCreateConversationManager:
    """Factory function tests."""

    @patch(
        "core.features.conversation.infrastructure.conversation_manager_factory.ConversationStore"
    )
    def test_creates_with_defaults(self, mock_store_cls: MagicMock) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mgr = create_conversation_manager(db_path=":memory:")
        assert mgr.max_cache_size == 100
        assert mgr.context_window_size == 50
        assert mgr.session_ttl == 3600

    @patch(
        "core.features.conversation.infrastructure.conversation_manager_factory.ConversationStore"
    )
    def test_creates_with_config(self, mock_store_cls: MagicMock) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        config = {
            "max_cache_size": 50,
            "context_window_size": 20,
            "session_ttl": 1800,
        }
        mgr = create_conversation_manager(db_path=":memory:", config=config)
        assert mgr.max_cache_size == 50
        assert mgr.context_window_size == 20
        assert mgr.session_ttl == 1800

    @patch(
        "core.features.conversation.infrastructure.conversation_manager_factory.ConversationStore"
    )
    def test_creates_with_none_config(self, mock_store_cls: MagicMock) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mgr = create_conversation_manager(db_path=":memory:", config=None)
        assert mgr.max_cache_size == 100  # default


class TestEventAdapterMixin:
    """Tests for event-to-message sender mapping."""

    @pytest.mark.asyncio
    async def test_group_assistant_message_uses_bot_identity(self) -> None:
        from astrbot.api.platform import MessageType

        mgr = ConversationManager(store=MagicMock())
        expected = MagicMock()
        mgr.add_message = AsyncMock(return_value=expected)

        event = MagicMock()
        event.unified_msg_origin = "group-1"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Alice"
        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        event.get_self_id.return_value = "bot-1"
        event.get_platform_name.return_value = "qq"
        event.message_obj.self_id = "bot-1"
        event.message_obj.sender.user_id = "user-1"
        event.message_obj.sender.nickname = "Alice"

        result = await mgr.add_message_from_event(
            event=event,
            role="assistant",
            content="bot reply",
        )

        assert result is expected
        mgr.add_message.assert_awaited_once()
        kwargs = mgr.add_message.await_args.kwargs
        assert kwargs["sender_id"] == "bot-1"
        assert kwargs["sender_name"] == "bot-1"
        assert kwargs["group_id"] == "group-1"
        assert kwargs["is_bot_message"] is True
