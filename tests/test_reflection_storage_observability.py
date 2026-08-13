"""反思写入重试的隐私安全观测契约测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_retry_skips_previously_completed_memory_candidates() -> None:
    """重试只写未完成候选，并报告成功、跳过与失败计数。"""
    from core.features.reflection.application.reflection_handler import (
        ReflectionHandler,
    )

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(
        side_effect=[
            0,
            {
                "completed_idempotency_keys": [
                    ReflectionHandler._memory_idempotency_key(
                        session_id="session-1",
                        start_index=0,
                        end_index=4,
                        batch_index=0,
                        memory_index=0,
                        content="memory-1",
                    )
                ]
            },
        ]
    )
    conversation_manager.update_session_metadata = AsyncMock(return_value=True)
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)

    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {"content": "memory-1", "importance": 0.8, "metadata": {}},
            {"content": "memory-2", "importance": 0.7, "metadata": {}},
        ]
    )

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=22)

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

    with patch(
        "core.features.reflection.application.reflection_handler.observability.report_debug_event"
    ) as report:
        await handler._storage_task(
            session_id="session-1",
            history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
            persona_id="persona",
            start_index=0,
            end_index=4,
            retry_count=1,
        )

    engine.add_memory.assert_awaited_once()
    assert engine.add_memory.await_args.kwargs["content"] == "memory-2"
    write_events = [
        call.kwargs
        for call in report.call_args_list
        if call.args == ("storage_task",)
        and call.kwargs.get("stage") == "memory_write"
        and call.kwargs.get("status") == "completed"
    ]
    assert write_events[-1]["success_count"] == 1
    assert write_events[-1]["skipped_count"] == 1
    assert write_events[-1]["failed_count"] == 0
