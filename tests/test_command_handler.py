"""core/command_handler.py 测试 — CommandHandler类。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================================
# CommandHandler — construction and routing tests
# ============================================================================


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

    @pytest.mark.asyncio
    async def test_quarantined_candidate_advances_window_without_canonical_write(
        self,
    ) -> None:
        """手动总结隔离低质量候选后不得写 canonical，但应安全推进窗口。"""

        from core.command_handler import CommandHandler
        from core.review.memory_quality_gate import MemoryGateResult

        conversation_manager = MagicMock()
        conversation_manager.store.get_message_count = AsyncMock(return_value=2)
        conversation_manager.get_session_metadata = AsyncMock(return_value=0)
        messages = [MagicMock(group_id=None), MagicMock(group_id=None)]
        conversation_manager.get_messages_range = AsyncMock(return_value=messages)
        conversation_manager.update_session_metadata = AsyncMock(return_value=True)
        conversation_manager.update_session_metadata_fields = AsyncMock(
            return_value=True
        )
        processor = MagicMock()
        processor.process_conversation = AsyncMock(
            return_value=[
                {
                    "content": "低质量候选",
                    "importance": 0.2,
                    "metadata": {"summary_quality": "low"},
                    "atoms": [],
                }
            ]
        )
        gate = MagicMock()
        gate.route_candidate = AsyncMock(
            return_value=MemoryGateResult(
                action="quarantined",
                candidate_id="qc-one",
                reason_codes=("summary_quality_low",),
            )
        )
        engine = MagicMock()
        engine.add_memory = AsyncMock()
        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=engine,
            conversation_manager=conversation_manager,
            index_validator=None,
            memory_processor=processor,
            memory_quality_gate=gate,
        )
        event = MagicMock()
        event.unified_msg_origin = "session-1"
        event.plain_result = MagicMock(side_effect=lambda message: message)

        with patch("core.utils.get_persona_id", AsyncMock(return_value="persona-1")):
            results = [result async for result in handler.handle_summarize(event)]

        assert len(results) == 2
        engine.add_memory.assert_not_awaited()
        gate.route_candidate.assert_awaited_once()
        conversation_manager.update_session_metadata_fields.assert_awaited_once_with(
            "session-1",
            {
                "last_summarized_index": 2,
                "pending_summary": None,
            },
        )


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


class TestHandleUpdate:
    """更新命令的安装与降级路径测试。"""

    @pytest.mark.asyncio
    async def test_apply_delegates_to_runtime_installer(self) -> None:
        """``apply`` 应启动安装器并返回安排重载的提示。"""
        from core.command_handler import CommandHandler

        manager = MagicMock()
        manager.is_enabled.return_value = True
        installer = MagicMock()
        installer.apply_latest = AsyncMock(
            return_value={"version": "1.1.0", "status": "reload_scheduled"}
        )
        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=None,
            conversation_manager=None,
            index_validator=None,
            update_manager=manager,
            update_installer=installer,
        )
        event = MagicMock()
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = [result async for result in handler.handle_update(event, "apply")]

        installer.apply_latest.assert_awaited_once_with()
        assert len(results) == 2
        assert results == ["update.applying", "update.apply_scheduled"]

    @pytest.mark.asyncio
    async def test_apply_reports_unavailable_without_installer(self) -> None:
        """宿主不支持单插件安装时，命令应返回不可用提示。"""
        from core.command_handler import CommandHandler

        manager = MagicMock()
        manager.is_enabled.return_value = True
        handler = CommandHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=None,
            conversation_manager=None,
            index_validator=None,
            update_manager=manager,
        )
        event = MagicMock()
        event.plain_result = MagicMock(side_effect=lambda message: message)

        results = [result async for result in handler.handle_update(event, "apply")]

        assert len(results) == 1
        assert results == ["update.unavailable"]

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
