"""反思窗口元数据提交与恢复状态的生产闭环回归。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.reflection.application import reflection_metadata as feature_metadata
from core.handlers import reflection_metadata as legacy_metadata


def test_legacy_handler_path_reuses_feature_application_objects() -> None:
    """旧 handlers 路径只能恒等导出反思窗口元数据服务。"""

    assert legacy_metadata.__all__ == feature_metadata.__all__
    for name in feature_metadata.__all__:
        assert getattr(legacy_metadata, name) is getattr(feature_metadata, name)


def _build_storage_handler(conversation_manager: MagicMock) -> Any:
    """构造仅用于元数据提交回归的反思处理器。"""

    from core.handlers.reflection_handler import ReflectionHandler

    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[{"content": "memory-1", "importance": 0.8, "metadata": {}}]
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=None)
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        enforce_limit_cb=MagicMock(),
    )
    handler._prepare_message_batches = AsyncMock(
        return_value=[[MagicMock(group_id=None)]]
    )
    return handler


async def _run_storage_task(handler: Any) -> None:
    """执行一个固定范围的反思存储窗口。"""

    await handler._storage_task(
        session_id="session-1",
        history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
        persona_id="persona",
        start_index=0,
        end_index=4,
        retry_count=0,
    )


class TestReflectionMetadataPersistence:
    """验证元数据提交失败时不会伪造完整成功或重复写 canonical。"""

    @pytest.mark.asyncio
    async def test_metadata_failure_preserves_written_keys_for_retry(self) -> None:
        """元数据连续失败后重试同窗不得再次写入已成功候选。"""

        conversation_manager = MagicMock()
        conversation_manager.get_session_metadata = AsyncMock(
            side_effect=lambda _session_id, key, default=None: (
                0 if key == "last_summarized_index" else default
            )
        )
        conversation_manager.update_session_metadata_fields = AsyncMock(
            side_effect=[False, False]
        )
        conversation_manager.update_session_metadata = AsyncMock(return_value=True)
        handler = _build_storage_handler(conversation_manager)

        with (
            patch(
                "core.features.reflection.application.reflection_metadata.report_debug_event"
            ) as metadata_report,
            patch(
                "core.handlers.reflection_handler.observability.report_debug_event"
            ) as storage_report,
            patch(
                "core.handlers.reflection_handler.resolve_continuity_session"
            ) as resolve_continuity,
        ):
            await _run_storage_task(handler)

            metadata_reason_codes = [
                call.kwargs.get("reason_code")
                for call in metadata_report.call_args_list
                if call.args == ("storage_task",)
            ]
            storage_reason_codes = [
                call.kwargs.get("reason_code")
                for call in storage_report.call_args_list
                if call.args == ("storage_task",)
            ]
            assert "summary_metadata_committed" not in metadata_reason_codes
            assert "summary_metadata_failed" in metadata_reason_codes
            assert "memories_stored" not in storage_reason_codes
            resolve_continuity.assert_not_called()

            pending_call = conversation_manager.update_session_metadata.await_args_list[
                -1
            ]
            assert pending_call.args[:2] == ("session-1", "pending_summary")
            pending_summary = pending_call.args[2]
            assert pending_summary["failed_stage"] == "metadata_commit"
            assert len(pending_summary["completed_idempotency_keys"]) == 1

            conversation_manager.get_session_metadata = AsyncMock(
                side_effect=lambda _session_id, key, default=None: (
                    0 if key == "last_summarized_index" else pending_summary
                )
            )
            conversation_manager.update_session_metadata_fields = AsyncMock(
                return_value=True
            )
            await _run_storage_task(handler)

        assert handler._memory_engine.add_memory.await_count == 1
        resolve_continuity.assert_called_once_with(handler._memory_engine, "session-1")

    @pytest.mark.asyncio
    async def test_retry_success_emits_terminal_committed_event(self) -> None:
        """首次原子提交失败而补救成功时必须发出 completed 终态。"""

        from core.features.reflection.application.reflection_metadata import (
            commit_summary_metadata,
        )

        conversation_manager = MagicMock()
        conversation_manager.update_session_metadata_fields = AsyncMock(
            side_effect=[False, True]
        )
        record_pending = AsyncMock(return_value=True)

        with (
            patch(
                "core.features.reflection.application.reflection_metadata.report_debug_event"
            ) as report_event,
            patch(
                "core.features.reflection.application.reflection_metadata.report_debug_exception"
            ) as report_exception,
        ):
            result = await commit_summary_metadata(
                conversation_manager,
                session_id="session-1",
                end_index=4,
                record_pending_summary=record_pending,
            )

        assert result is True
        record_pending.assert_not_awaited()
        assert any(
            call.kwargs.get("reason_code") == "summary_metadata_retrying"
            for call in report_exception.call_args_list
        )
        assert any(
            call.kwargs.get("reason_code") == "summary_metadata_committed"
            and call.kwargs.get("status") == "completed"
            for call in report_event.call_args_list
        )

    @pytest.mark.asyncio
    async def test_metadata_commit_cancellation_propagates(self) -> None:
        """原子元数据写入被取消时不得记录 pending 或降级为普通失败。"""

        from core.features.reflection.application.reflection_metadata import (
            commit_summary_metadata,
        )

        conversation_manager = MagicMock()
        conversation_manager.update_session_metadata_fields = AsyncMock(
            side_effect=asyncio.CancelledError
        )
        record_pending = AsyncMock(return_value=True)

        with pytest.raises(asyncio.CancelledError):
            await commit_summary_metadata(
                conversation_manager,
                session_id="session-1",
                end_index=4,
                record_pending_summary=record_pending,
            )

        record_pending.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_record_cancellation_propagates(self) -> None:
        """恢复窗口写入被取消时必须原样传播取消。"""

        from core.features.reflection.application.reflection_metadata import (
            persist_pending_summary,
        )

        conversation_manager = MagicMock()
        conversation_manager.update_session_metadata = AsyncMock(
            side_effect=asyncio.CancelledError
        )

        with pytest.raises(asyncio.CancelledError):
            await persist_pending_summary(
                conversation_manager,
                session_id="session-1",
                start_index=0,
                end_index=4,
                current_retry_count=0,
            )
