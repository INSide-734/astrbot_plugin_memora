"""core/event_handler.py 测试 — EventHandler 类。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestEventHandlerConstruction:
    """验证 EventHandler 初始化与子组件装配。"""

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

    def test_event_handler_passes_evolution_manager_to_reflection(self) -> None:
        from core.event_handler import EventHandler

        manager = MagicMock()
        handler = EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            memory_evolution_manager=manager,
        )

        assert handler._memory_evolution_manager is manager
        assert handler._reflection_handler._memory_evolution_manager is manager

    @pytest.mark.asyncio
    async def test_reflection_schedules_only_after_reloaded_canonical_source(
        self,
    ) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        source = MagicMock(memory_id=17)
        manager = MagicMock()
        manager.store.load_sources = AsyncMock(return_value=[source])
        manager.schedule_consider = AsyncMock()
        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            memory_evolution_manager=manager,
        )

        await handler._schedule_evolution_after_write(17)

        manager.store.load_sources.assert_awaited_once_with((17,))
        manager.schedule_consider.assert_awaited_once_with(source)

    @pytest.mark.asyncio
    async def test_reflection_skips_schedule_when_canonical_source_missing(
        self,
    ) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        manager = MagicMock()
        manager.store.load_sources = AsyncMock(return_value=[])
        manager.schedule_consider = AsyncMock()
        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            memory_evolution_manager=manager,
        )

        await handler._schedule_evolution_after_write(17)

        manager.schedule_consider.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reflection_schedule_propagates_cancelled_error(self) -> None:
        from core.handlers.reflection_handler import ReflectionHandler

        manager = MagicMock()
        manager.store.load_sources = AsyncMock(side_effect=asyncio.CancelledError())
        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            memory_evolution_manager=manager,
        )

        with pytest.raises(asyncio.CancelledError):
            await handler._schedule_evolution_after_write(17)


class TestEventHandlerShutdown:
    """验证关闭与会话重置行为。"""

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
        # 用替身隔离反思处理器的关闭行为。
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

        # 普通错误会被记录，不应向调用方抛出。
        await handler.handle_session_reset(event)
        conv.clear_session.assert_awaited_once()


class TestEventHandlerGroupMessages:
    """验证群聊消息捕获入口。"""

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
        # 功能关闭时应提前返回，不提取消息内容。
        assert event.get_message_type.call_count == 0

    @pytest.mark.asyncio
    async def test_skips_non_group_messages(self) -> None:
        from astrbot.api.platform import MessageType

        from core.event_handler import EventHandler

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
        # 非群聊消息应提前返回。
        assert event.get_sender_id.call_count == 0

    @pytest.mark.asyncio
    async def test_skips_messages_from_self(self) -> None:
        from astrbot.api.platform import MessageType

        from core.event_handler import EventHandler

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
        # 发送者为机器人自身时应跳过。


class TestEventHandlerMemoryRecall:
    """验证记忆召回委托。"""

    @pytest.mark.asyncio
    async def test_delegates_to_recall_handler(self) -> None:
        """召回入口应准备身份并把同一快照传给子处理器。"""

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
        call = handler._recall_handler.handle_memory_recall.await_args
        assert call.args == (event, req)
        assert call.kwargs["identity"].trust_status.value == "unsupported"


class TestEventHandlerMemoryReflection:
    """验证记忆反思委托。"""

    @pytest.mark.asyncio
    async def test_delegates_to_reflection_handler(self) -> None:
        """反思入口应准备身份并把同一快照传给子处理器。"""

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
        call = handler._reflection_handler.handle_memory_reflection.await_args
        assert call.args == (event, resp)
        assert call.kwargs["identity"].trust_status.value == "unsupported"


class TestEnforceMessageLimit:
    """验证消息数量上限维护。"""

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
        # 未超过上限时应提前返回，不调用 Store 清理方法。
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
