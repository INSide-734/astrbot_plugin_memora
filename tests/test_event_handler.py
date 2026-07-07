"""core/event_handler.py 测试 — EventHandler 类。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEventHandlerConstruction:
    """Tests for EventHandler.__init__ and component wiring."""

    def test_event_handler_creates_sub_handlers(self) -> None:
        from core.event_handler import EventHandler

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        assert handler._recall_handler is not None
        assert handler._reflection_handler is not None
        assert handler._dedup is not None
        assert handler._extractor is not None
        assert handler._cleaner is not None

    def test_event_handler_stores_dependencies(self) -> None:
        from core.event_handler import EventHandler

        ctx = MagicMock()
        cfg = MagicMock()
        engine = MagicMock()
        proc = MagicMock()
        conv = MagicMock()

        handler = EventHandler(
            context=ctx,
            config_manager=cfg,
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
        )
        assert handler.context is ctx
        assert handler.config_manager is cfg
        assert handler.memory_engine is engine
        assert handler.memory_processor is proc
        assert handler.conversation_manager is conv


class TestEventHandlerShutdown:
    """Tests for shutdown() and session reset."""

    @pytest.mark.asyncio
    async def test_shutdown_delegates_to_reflection_handler(self) -> None:
        from core.event_handler import EventHandler

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        # Replace the reflection handler shutdown with a mock
        handler._reflection_handler.shutdown = AsyncMock()
        await handler.shutdown()
        handler._reflection_handler.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_maintenance_tasks(self) -> None:
        from core.event_handler import EventHandler

        completed = asyncio.Event()

        async def _maintenance() -> None:
            await asyncio.sleep(0)
            completed.set()

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        handler._reflection_handler.shutdown = AsyncMock()
        handler._create_maintenance_task(_maintenance(), name="test-maintenance")

        await handler.shutdown()

        assert completed.is_set()
        assert handler._maintenance_tasks == set()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_maintenance_tasks(self) -> None:
        from core.event_handler import EventHandler

        cancelled = asyncio.Event()

        async def _maintenance() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        handler._reflection_handler.shutdown = AsyncMock()
        handler._create_maintenance_task(
            _maintenance(),
            name="test-pending-maintenance",
        )
        await asyncio.sleep(0)

        await handler.shutdown()

        assert cancelled.is_set()
        assert handler._maintenance_tasks == set()

    @pytest.mark.asyncio
    async def test_handle_session_reset_with_valid_session(self) -> None:
        from core.event_handler import EventHandler

        ctx = MagicMock()
        cfg = MagicMock()
        engine = MagicMock()
        proc = MagicMock()
        conv = MagicMock()
        conv.clear_session = AsyncMock()

        handler = EventHandler(
            context=ctx,
            config_manager=cfg,
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
        )
        event = MagicMock()
        event.unified_msg_origin = "test-session-001"

        await handler.handle_session_reset(event)
        conv.clear_session.assert_awaited_once_with("test-session-001")

    @pytest.mark.asyncio
    async def test_handle_session_reset_with_empty_session(self) -> None:
        from core.event_handler import EventHandler

        ctx = MagicMock()
        cfg = MagicMock()
        engine = MagicMock()
        proc = MagicMock()
        conv = MagicMock()
        conv.clear_session = AsyncMock()

        handler = EventHandler(
            context=ctx,
            config_manager=cfg,
            memory_engine=engine,
            memory_processor=proc,
            conversation_manager=conv,
        )
        event = MagicMock()
        event.unified_msg_origin = ""

        await handler.handle_session_reset(event)
        conv.clear_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_session_reset_handles_errors(self) -> None:
        from core.event_handler import EventHandler

        conv = MagicMock()
        conv.clear_session = AsyncMock(side_effect=RuntimeError("DB error"))

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=conv,
        )
        event = MagicMock()
        event.unified_msg_origin = "test-session-001"

        # Should not raise; error is caught and logged
        await handler.handle_session_reset(event)
        conv.clear_session.assert_awaited_once()


class TestEventHandlerGroupMessages:
    """Tests for handle_all_group_messages."""

    @pytest.mark.asyncio
    async def test_skips_when_full_group_capture_disabled(self) -> None:
        from core.event_handler import EventHandler

        cfg = MagicMock()
        cfg.get.return_value = False  # enable_full_group_capture = False

        handler = EventHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        event = MagicMock()

        await handler.handle_all_group_messages(event)
        # Should return early, not extract content
        assert event.get_message_type.call_count == 0

    @pytest.mark.asyncio
    async def test_skips_non_group_messages(self) -> None:
        from core.event_handler import EventHandler
        from astrbot.api.platform import MessageType

        cfg = MagicMock()
        cfg.get.return_value = True  # enable_full_group_capture = True

        handler = EventHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        event = MagicMock()
        event.get_message_type.return_value = MessageType.FRIEND_MESSAGE

        await handler.handle_all_group_messages(event)
        # Should return early on non-group message
        assert event.get_sender_id.call_count == 0

    @pytest.mark.asyncio
    async def test_skips_messages_from_self(self) -> None:
        from core.event_handler import EventHandler
        from astrbot.api.platform import MessageType

        cfg = MagicMock()
        cfg.get.return_value = True

        handler = EventHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        event = MagicMock()
        event.get_message_type.return_value = MessageType.GROUP_MESSAGE
        event.get_sender_id.return_value = "bot_self"
        event.get_self_id.return_value = "bot_self"

        await handler.handle_all_group_messages(event)
        # Should skip when sender is self


class TestEventHandlerMemoryRecall:
    """Tests for handle_memory_recall delegation."""

    @pytest.mark.asyncio
    async def test_delegates_to_recall_handler(self) -> None:
        from core.event_handler import EventHandler

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        handler._recall_handler.handle_memory_recall = AsyncMock()

        event = MagicMock()
        req = MagicMock()

        await handler.handle_memory_recall(event, req)
        handler._recall_handler.handle_memory_recall.assert_awaited_once_with(
            event, req
        )


class TestEventHandlerMemoryReflection:
    """Tests for handle_memory_reflection delegation."""

    @pytest.mark.asyncio
    async def test_delegates_to_reflection_handler(self) -> None:
        from core.event_handler import EventHandler

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
        )
        handler._reflection_handler.handle_memory_reflection = AsyncMock()

        event = MagicMock()
        resp = MagicMock()

        await handler.handle_memory_reflection(event, resp)
        handler._reflection_handler.handle_memory_reflection.assert_awaited_once_with(
            event, resp
        )


class TestEnforceMessageLimit:
    """Tests for _enforce_message_limit."""

    @pytest.mark.asyncio
    async def test_skips_when_no_conversation_manager(self) -> None:
        from core.event_handler import EventHandler

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=None,
        )
        # Should return early, not calling store methods
        result = await handler._enforce_message_limit("test-session")
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_no_store(self) -> None:
        from core.event_handler import EventHandler

        conv = MagicMock()
        conv.store = None

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=conv,
        )
        result = await handler._enforce_message_limit("test-session")
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_no_connection(self) -> None:
        from core.event_handler import EventHandler

        conv = MagicMock()
        conv.store = MagicMock()
        conv.store.connection = None

        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=conv,
        )
        result = await handler._enforce_message_limit("test-session")
        assert result is None
