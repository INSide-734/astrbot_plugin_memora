"""event_adapter 测试 — EventAdapterMixin 从 AstrBot 事件创建消息。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.event_adapter import EventAdapterMixin


# ---------------------------------------------------------------------------
# Helper: concrete class for testing the mixin
# ---------------------------------------------------------------------------


class _TestAdapter(EventAdapterMixin):
    """Concrete class that provides the dependencies the mixin needs."""

    def __init__(self, store=None):
        self.store = store or MagicMock()
        self._session_id_captured = None
        self._call_kwargs = None

    async def add_message(self, session_id, role, content, sender_id=None,
                          sender_name=None, group_id=None, platform="unknown",
                          is_bot_message=False):
        """Record the call args for assertion, return a fake Message."""
        self._session_id_captured = session_id
        self._call_kwargs = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "group_id": group_id,
            "platform": platform,
            "is_bot_message": is_bot_message,
        }
        msg = MagicMock()
        msg.id = 42
        return msg


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


class TestEventAdapterStructure:
    """Smoke tests for EventAdapterMixin structure."""

    def test_add_message_from_event_exists(self) -> None:
        """add_message_from_event method is defined on the mixin."""
        assert hasattr(EventAdapterMixin, "add_message_from_event")
        method = getattr(EventAdapterMixin, "add_message_from_event")
        assert callable(method)

    def test_method_signature(self) -> None:
        """Method accepts event, role, content parameters."""
        import inspect

        sig = inspect.signature(EventAdapterMixin.add_message_from_event)
        params = list(sig.parameters.keys())
        assert "event" in params
        assert "role" in params
        assert "content" in params


# ---------------------------------------------------------------------------
# add_message_from_event tests
# ---------------------------------------------------------------------------


class TestAddMessageFromEvent:
    """Tests for add_message_from_event async method."""

    @pytest.mark.asyncio
    async def test_basic_user_message(self) -> None:
        """Basic user message with sender name from event."""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-001"
        event.get_sender_id.return_value = "user-123"
        event.get_sender_name.return_value = "Alice"
        event.get_message_type.return_value = "PRIVATE_MESSAGE"  # default type
        event.get_platform_name.return_value = "test-platform"

        msg = await adapter.add_message_from_event(event, "user", "Hello world")
        assert msg is not None
        assert adapter._call_kwargs is not None
        assert adapter._call_kwargs["role"] == "user"

    @pytest.mark.asyncio
    async def test_uses_unified_msg_origin(self) -> None:
        """Session ID comes from event.unified_msg_origin."""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "group-abc-123"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Bob"
        event.get_message_type.return_value = "PRIVATE_MESSAGE"
        event.get_platform_name.return_value = "qq"

        await adapter.add_message_from_event(event, "user", "test")
        assert adapter._call_kwargs["session_id"] == "group-abc-123"

    @pytest.mark.asyncio
    async def test_falls_back_to_session_id_for_sender(self) -> None:
        """When no sender_id is available, uses session_id as fallback."""
        adapter = _TestAdapter()
        # Use spec to strictly control available attributes
        event = MagicMock(spec=["unified_msg_origin", "get_message_type",
                                 "get_sender_name", "message_obj"])
        event.unified_msg_origin = "session-fallback"
        event.get_sender_name.return_value = None
        event.get_message_type.return_value = "PRIVATE_MESSAGE"
        event.message_obj = MagicMock(spec=["sender", "raw_message"])
        event.message_obj.sender = MagicMock()
        event.message_obj.raw_message = MagicMock()

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["sender_id"] == "session-fallback"

    @pytest.mark.asyncio
    async def test_group_message_detection(self) -> None:
        """Group messages set group_id and detect is_group correctly."""
        adapter = _TestAdapter()
        from astrbot.api.platform import MessageType

        event = MagicMock()
        event.unified_msg_origin = "group-456"
        event.get_sender_id.return_value = "user-789"
        event.get_sender_name.return_value = "Charlie"
        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        event.get_platform_name.return_value = "qq"

        await adapter.add_message_from_event(event, "user", "group message")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["group_id"] == "group-456"

    @pytest.mark.asyncio
    async def test_bot_message_in_group(self) -> None:
        """Bot message in group gets self_id as sender."""
        adapter = _TestAdapter()
        from astrbot.api.platform import MessageType

        event = MagicMock()
        event.unified_msg_origin = "group-789"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Unknown"
        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        event.get_platform_name.return_value = "qq"
        event.get_self_id.return_value = "bot-999"
        event.message_obj = MagicMock()
        event.message_obj.self_id = "bot-999"

        await adapter.add_message_from_event(event, "assistant", "bot reply")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["sender_id"] == "bot-999"

    @pytest.mark.asyncio
    async def test_bot_message_in_private_chat(self) -> None:
        """Bot message in private chat keeps normal behavior."""
        adapter = _TestAdapter()
        from astrbot.api.platform import MessageType

        event = MagicMock()
        event.unified_msg_origin = "private-001"
        event.get_sender_id.return_value = "assistant-bot"
        event.get_sender_name.return_value = "BotName"
        event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
        event.get_platform_name.return_value = "qq"
        event.get_self_id.return_value = "bot-999"

        await adapter.add_message_from_event(event, "assistant", "private reply")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["is_bot_message"] is True
        assert call_kwargs["group_id"] is None

    @pytest.mark.asyncio
    async def test_platform_unknown_fallback(self) -> None:
        """When no get_platform_name, falls back to 'unknown'."""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-x"
        event.get_sender_id.return_value = "user-1"
        event.get_sender_name.return_value = "Test"
        event.get_message_type.return_value = "PRIVATE_MESSAGE"
        # No get_platform_name method
        del event.get_platform_name

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["platform"] == "unknown"

    @pytest.mark.asyncio
    async def test_sender_id_from_attr_fallback(self) -> None:
        """When get_sender_id is not available, uses sender_id attribute."""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-y"
        event.sender_id = "attr-sender-123"
        del event.get_sender_id  # remove method
        event.get_message_type.return_value = "PRIVATE_MESSAGE"

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["sender_id"] == "attr-sender-123"

    @pytest.mark.asyncio
    async def test_sender_name_from_attr_fallback(self) -> None:
        """When get_sender_name is not available, uses sender_name attribute."""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-z"
        event.get_sender_id.return_value = "sender-1"
        event.sender_name = "AttrName"
        del event.get_sender_name  # remove method
        event.get_message_type.return_value = "PRIVATE_MESSAGE"

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["sender_name"] == "AttrName"

    @pytest.mark.asyncio
    async def test_platform_from_attr_fallback(self) -> None:
        """When get_platform_name is not available, uses platform attribute."""
        adapter = _TestAdapter()
        event = MagicMock()
        event.unified_msg_origin = "session-p"
        event.get_sender_id.return_value = "sender-1"
        event.get_sender_name.return_value = "Name"
        event.get_platform_name = MagicMock(return_value="dotnet")
        event.get_message_type.return_value = "PRIVATE_MESSAGE"

        await adapter.add_message_from_event(event, "user", "test")
        call_kwargs = adapter._call_kwargs
        assert call_kwargs["platform"] == "dotnet"
