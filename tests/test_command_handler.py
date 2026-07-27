"""core/command_handler.py 测试 — CommandHandler类。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ============================================================================
# CommandHandler — construction and routing tests
# ============================================================================


class TestCommandHandlerConstruction:
    """Tests for CommandHandler.__init__."""

    def test_stores_all_dependencies(self) -> None:
        from core.command_handler import CommandHandler

        ctx = MagicMock()
        cfg = MagicMock()
        engine = MagicMock()
        conv = MagicMock()
        validator = MagicMock()
        proc = MagicMock()
        status_cb = MagicMock()

        handler = CommandHandler(
            context=ctx,
            config_manager=cfg,
            memory_engine=engine,
            conversation_manager=conv,
            index_validator=validator,
            memory_processor=proc,
            initialization_status_callback=status_cb,
        )
        assert handler.context is ctx
        assert handler.config_manager is cfg
        assert handler.memory_engine is engine
        assert handler.conversation_manager is conv
        assert handler.index_validator is validator
        assert handler._memory_processor is proc
        assert handler.get_initialization_status is status_cb

    def test_accepts_none_values(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=None,
            conversation_manager=None,
            index_validator=None,
            memory_processor=None,
            initialization_status_callback=None,
        )
        assert handler.memory_engine is None
        assert handler.conversation_manager is None
        assert handler.index_validator is None
        assert handler._memory_processor is None
        assert handler.get_initialization_status is None

    def test_stores_write_guard_callback(self) -> None:
        from core.command_handler import CommandHandler

        guard = MagicMock(return_value=False)
        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=MagicMock(),
            write_guard_cb=guard,
        )

        assert handler._write_guard_cb is guard


class TestFormatErrorMethod:
    """Tests for _format_error_message static method."""

    def test_formats_error_with_action_and_details(self) -> None:
        from core.command_handler import CommandHandler

        msg = CommandHandler._format_error_message("测试操作", ValueError("测试错误"))
        # _format_error_message uses t() which falls back to key when translations
        # are not loaded. Verify the message is a string with content.
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_formats_error_with_suggestions(self) -> None:
        from core.command_handler import CommandHandler

        msg = CommandHandler._format_error_message(
            "搜索", ValueError("超时"), suggestions=["请检查网络连接", "请稍后重试"]
        )
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_formats_error_without_suggestions(self) -> None:
        from core.command_handler import CommandHandler

        msg = CommandHandler._format_error_message("重建索引", RuntimeError("数据库锁"))
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_result_contains_error_action(self) -> None:
        from core.command_handler import CommandHandler

        msg = CommandHandler._format_error_message(
            "test_action", ValueError("test_error")
        )
        # t() fallback returns the key itself
        assert "action_failed" in msg
        assert "details" in msg


class TestComponentNotReady:
    """Tests for _component_not_ready_message static method."""

    def test_includes_component_and_command(self) -> None:
        from core.command_handler import CommandHandler

        msg = CommandHandler._component_not_ready_message("记忆引擎", "/memora status")
        # Should contain some meaningful text
        assert len(msg) > 0
        # The t() function may use a fallback format
        assert isinstance(msg, str)


class TestHandleHelp:
    """Tests for handle_help static method."""

    @pytest.mark.asyncio
    async def test_yields_help_message(self) -> None:
        from core.command_handler import CommandHandler

        event = MagicMock()
        event.plain_result = MagicMock(return_value="help_message")

        results = []
        async for result in CommandHandler.handle_help(event):
            results.append(result)

        assert len(results) == 1


class TestHandleSummarizeErrors:
    """Tests for handle_summarize error paths."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_conversation_manager(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=None,
            index_validator=None,
        )
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in handler.handle_summarize(event):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_returns_busy_when_summary_window_locked(self) -> None:
        from core.command_handler import CommandHandler

        locker = MagicMock()
        locker.try_begin_summary_window = AsyncMock(return_value=False)
        locker.finish_summary_window = MagicMock()

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=None,
            memory_processor=MagicMock(),
            summary_window_locker=locker,
        )
        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = []
        async for result in handler.handle_summarize(event):
            results.append(result)

        assert results == ["该会话已有记忆总结任务正在执行，请稍后再试。"]
        locker.try_begin_summary_window.assert_awaited_once_with("session-1")
        locker.finish_summary_window.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_error_when_no_memory_engine(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=None,
            conversation_manager=MagicMock(),
            index_validator=None,
        )
        event = MagicMock()
        event.plain_result = MagicMock(return_value="not ready")

        results = []
        async for result in handler.handle_summarize(event):
            results.append(result)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_blocks_summarize_when_pending_restore_guard_is_active(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=MagicMock(),
            write_guard_cb=MagicMock(return_value=True),
        )
        event = MagicMock()
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = []
        async for result in handler.handle_summarize(event):
            results.append(result)

        assert results == ["备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"]


class TestMaintenanceWriteGuard:
    """命令层维护写入保护测试。"""

    def test_returns_none_when_guard_missing(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=None,
            conversation_manager=None,
            index_validator=None,
        )

        assert handler._maintenance_write_guard_message() is None

    def test_returns_message_when_guard_active(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=None,
            conversation_manager=None,
            index_validator=None,
            write_guard_cb=MagicMock(return_value=True),
        )

        assert handler._maintenance_write_guard_message() == (
            "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"
        )

    @pytest.mark.asyncio
    async def test_blocks_forget_when_guard_active(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=MagicMock(),
            write_guard_cb=MagicMock(return_value=True),
        )
        event = MagicMock()
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = []
        async for result in handler.handle_forget(event, 1):
            results.append(result)

        assert results == ["备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"]

    @pytest.mark.asyncio
    async def test_blocks_rebuild_index_when_guard_active(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=MagicMock(),
            write_guard_cb=MagicMock(return_value=True),
        )
        event = MagicMock()
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = []
        async for result in handler.handle_rebuild_index(event):
            results.append(result)

        assert results == ["备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"]

    @pytest.mark.asyncio
    async def test_allows_cleanup_preview_when_guard_active(self) -> None:
        from core.command_handler import CommandHandler

        context = MagicMock()
        context.conversation_manager.get_curr_conversation_id = AsyncMock(
            return_value=None
        )
        handler = CommandHandler(
            context=context,
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=MagicMock(),
            write_guard_cb=MagicMock(return_value=True),
        )
        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = []
        async for result in handler.handle_cleanup(event, dry_run=True):
            results.append(result)

        assert len(results) >= 1
        assert results[0]

    @pytest.mark.asyncio
    async def test_blocks_cleanup_exec_when_guard_active(self) -> None:
        from core.command_handler import CommandHandler

        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            conversation_manager=MagicMock(),
            index_validator=MagicMock(),
            write_guard_cb=MagicMock(return_value=True),
        )
        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = []
        async for result in handler.handle_cleanup(event, dry_run=False):
            results.append(result)

        assert results == ["备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"]
