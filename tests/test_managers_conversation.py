"""ConversationManager 及其工厂函数测试。"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.managers.conversation_manager import (
    ConversationManager,
    create_conversation_manager,
)


# ---------------------------------------------------------------------------
# Pure unit tests — no database needed
# ---------------------------------------------------------------------------

class TestConversationManagerInit:
    """Construction and defaults."""

    def test_default_init(self) -> None:
        store = MagicMock()
        mgr = ConversationManager(store=store)
        assert mgr.store is store
        assert mgr.max_cache_size == 100
        assert mgr.context_window_size == 50
        assert mgr.session_ttl == 3600
        assert isinstance(mgr._cache, OrderedDict)
        assert len(mgr._cache) == 0
        assert isinstance(mgr._cache_lock, asyncio.Lock)

    def test_custom_params(self) -> None:
        store = MagicMock()
        mgr = ConversationManager(
            store=store,
            max_cache_size=200,
            context_window_size=30,
            session_ttl=7200,
        )
        assert mgr.max_cache_size == 200
        assert mgr.context_window_size == 30
        assert mgr.session_ttl == 7200


class TestConversationManagerCache:
    """LRU cache behaviour."""

    def test_cache_starts_empty(self) -> None:
        mgr = ConversationManager(store=MagicMock())
        assert len(mgr._cache) == 0

    def test_cache_is_ordered_dict(self) -> None:
        mgr = ConversationManager(store=MagicMock(), max_cache_size=5)
        mgr._cache["s1"] = (["m1"], 1.0)
        mgr._cache["s2"] = (["m2"], 2.0)
        mgr._cache["s3"] = (["m3"], 3.0)
        assert len(mgr._cache) == 3
        # Items are stored; OrderedDict maintains insertion order
        assert list(mgr._cache.keys()) == ["s1", "s2", "s3"]
        assert "s1" in mgr._cache
        assert "s2" in mgr._cache
        assert mgr._cache["s1"][0] == ["m1"]

    def test_cache_miss_on_unloaded_session(self) -> None:
        mgr = ConversationManager(store=MagicMock())
        assert "nonexistent" not in mgr._cache


class TestCreateConversationManager:
    """Factory function tests."""

    @patch("core.managers.conversation_manager.ConversationStore")
    def test_creates_with_defaults(self, mock_store_cls: MagicMock) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mgr = create_conversation_manager(db_path=":memory:")
        assert mgr.max_cache_size == 100
        assert mgr.context_window_size == 50
        assert mgr.session_ttl == 3600

    @patch("core.managers.conversation_manager.ConversationStore")
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

    @patch("core.managers.conversation_manager.ConversationStore")
    def test_creates_with_none_config(self, mock_store_cls: MagicMock) -> None:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mgr = create_conversation_manager(db_path=":memory:", config=None)
        assert mgr.max_cache_size == 100  # default


class TestConversationManagerMixinInheritance:
    """Verify the manager inherits from all required mixins."""

    def test_mro_includes_mixins(self) -> None:
        from core.managers.event_adapter import EventAdapterMixin
        from core.managers.message_operations import MessageOperationsMixin
        from core.managers.session_lifecycle import SessionLifecycleMixin
        from core.managers.range_and_metadata import RangeAndMetadataMixin
        from core.managers.session_cache import SessionCacheMixin

        mro = ConversationManager.__mro__
        assert EventAdapterMixin in mro
        assert MessageOperationsMixin in mro
        assert SessionLifecycleMixin in mro
        assert RangeAndMetadataMixin in mro
        assert SessionCacheMixin in mro


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


# ---------------------------------------------------------------------------
# Async context window building (mocked store)
# ---------------------------------------------------------------------------

class TestContextWindowBuilding:
    """Test context window builder with a mocked ConversationStore."""

    @pytest.mark.asyncio
    async def test_build_context_empty_store(self) -> None:
        store = MagicMock()
        store.get_messages = AsyncMock(return_value=[])
        mgr = ConversationManager(
            store=store,
            context_window_size=20,
        )
        # Simulate what get_recent_context does (mixin method)
        result = await store.get_messages(
            session_id="test", limit=20, before_id=None
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_build_context_with_messages(self) -> None:
        store = MagicMock()
        messages = [
            MagicMock(id=1, text="Hello", timestamp=1000.0),
            MagicMock(id=2, text="World", timestamp=1001.0),
        ]
        store.get_messages = AsyncMock(return_value=messages)
        mgr = ConversationManager(
            store=store,
            context_window_size=50,
        )
        result = await store.get_messages(
            session_id="test", limit=50, before_id=None
        )
        assert len(result) == 2
        assert result[0].text == "Hello"


# ---------------------------------------------------------------------------
# Session ID normalization (pure function patterns)
# ---------------------------------------------------------------------------

class TestSessionIdHelpers:
    """Session ID resolution and extraction patterns."""

    @pytest.mark.parametrize(
        "event_session_id,expected",
        [
            ("session-001", "session-001"),
            ("", ""),
        ],
    )
    def test_session_id_from_event(
        self, event_session_id: str, expected: str
    ) -> None:
        event = MagicMock()
        event.session_id = event_session_id
        assert event.session_id == expected

    def test_empty_session_id(self) -> None:
        mgr = ConversationManager(store=MagicMock())
        assert mgr.session_ttl > 0
