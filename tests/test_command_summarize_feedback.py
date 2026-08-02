"""手动总结命令的 canonical 写入与隔离反馈契约测试。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core import i18n_backend
from core.command_handler import CommandHandler
from core.review.memory_quality_gate import MemoryGateResult


@pytest.fixture(autouse=True)
def _load_chinese_i18n() -> Iterator[None]:
    """为反馈断言加载中文资源，并在用例结束后恢复全局状态。

    生成:
        测试执行控制权；清理阶段恢复进入用例前的翻译状态。
    """
    original_state = (
        i18n_backend._fallback,
        i18n_backend._translations,
        i18n_backend._current_lang,
    )
    i18n_backend.init("zh")
    try:
        yield
    finally:
        (
            i18n_backend._fallback,
            i18n_backend._translations,
            i18n_backend._current_lang,
        ) = original_state


def _candidate(
    content: str,
    *,
    importance: float,
    topics: list[str],
) -> dict[str, Any]:
    """构造包含反馈字段的最小记忆候选。

    参数:
        content: 候选正文。
        importance: 用于成功反馈的候选重要性。
        topics: 用于成功反馈的候选主题。

    返回:
        可由 ``CommandHandler`` 处理的候选字典。
    """
    return {
        "content": content,
        "importance": importance,
        "metadata": {"topics": topics},
        "atoms": [],
    }


def _build_summary_case(
    *,
    candidates: list[dict[str, Any]],
    gate_actions: list[str],
    actual_count: int,
    last_summarized_index: int,
    add_side_effect: Exception | None = None,
) -> tuple[CommandHandler, MagicMock, MagicMock, MagicMock]:
    """装配不访问真实 Provider 或存储的手动总结场景。

    参数:
        candidates: 处理器返回的候选列表。
        gate_actions: 与候选顺序对应的质量门动作。
        actual_count: 会话当前真实消息数。
        last_summarized_index: 执行前的总结进度。
        add_side_effect: canonical 写入时需要注入的异常。

    返回:
        命令处理器、事件、会话管理器和记忆引擎替身。
    """
    conversation_manager = MagicMock()
    conversation_manager.store.get_message_count = AsyncMock(return_value=actual_count)
    conversation_manager.get_session_metadata = AsyncMock(
        return_value=last_summarized_index
    )
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[MagicMock(group_id=None), MagicMock(group_id=None)]
    )
    conversation_manager.update_session_metadata = AsyncMock()

    processor = MagicMock()
    processor.process_conversation = AsyncMock(return_value=candidates)

    gate = MagicMock()
    gate.route_candidate = AsyncMock(
        side_effect=[
            MemoryGateResult(
                action=action,
                candidate_id=f"candidate-{index}" if action == "quarantined" else None,
            )
            for index, action in enumerate(gate_actions)
        ]
    )

    engine = MagicMock()
    engine.add_memory = AsyncMock(side_effect=add_side_effect)
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
    event.unified_msg_origin = "session-feedback"
    event.plain_result = MagicMock(side_effect=lambda message: message)
    return handler, event, conversation_manager, engine


async def _run_summary(handler: CommandHandler, event: MagicMock) -> list[str]:
    """执行手动总结并收集命令产生的全部文本结果。

    参数:
        handler: 已装配依赖的命令处理器。
        event: 当前测试使用的事件替身。

    返回:
        命令依次产生的文本结果。
    """
    with patch("core.utils.get_persona_id", AsyncMock(return_value="persona-1")):
        return [result async for result in handler.handle_summarize(event)]


@pytest.mark.asyncio
async def test_quarantine_only_reports_no_canonical_write_and_advances() -> None:
    """只有隔离候选时应明确未写入长期记忆，但安全推进窗口。"""
    handler, event, conversation_manager, engine = _build_summary_case(
        candidates=[
            _candidate("低质量候选", importance=0.2, topics=["隔离主题"]),
        ],
        gate_actions=["quarantined"],
        actual_count=8,
        last_summarized_index=6,
    )

    results = await _run_summary(handler, event)

    feedback = results[-1]
    assert "未写入长期记忆" in feedback
    assert "隔离候选: 1 条" in feedback
    assert "复核队列" in feedback
    assert "记忆总结完成" not in feedback
    assert "第 8 条消息" in feedback
    engine.add_memory.assert_not_awaited()
    conversation_manager.update_session_metadata.assert_any_await(
        "session-feedback", "last_summarized_index", 8
    )


@pytest.mark.asyncio
async def test_mixed_result_reports_canonical_and_quarantine_separately() -> None:
    """混合结果应分别报告长期记忆与隔离数量，并排除隔离统计。"""
    handler, event, conversation_manager, engine = _build_summary_case(
        candidates=[
            _candidate("已验证事实", importance=0.8, topics=["有效主题"]),
            _candidate("待复核事实", importance=0.2, topics=["隔离主题"]),
        ],
        gate_actions=["allowed", "quarantined"],
        actual_count=12,
        last_summarized_index=8,
    )

    results = await _run_summary(handler, event)

    feedback = results[-1]
    assert "已写入长期记忆: 1 条" in feedback
    assert "隔离候选: 1 条" in feedback
    assert "重要性: 0.80" in feedback
    assert "有效主题" in feedback
    assert "隔离主题" not in feedback
    assert "第 12 条消息" in feedback
    engine.add_memory.assert_awaited_once()
    conversation_manager.update_session_metadata.assert_any_await(
        "session-feedback", "last_summarized_index", 12
    )


@pytest.mark.asyncio
async def test_canonical_only_reports_real_message_progress() -> None:
    """纯 canonical 成功反馈应使用真实消息进度，而不是候选数量。"""
    handler, event, _, engine = _build_summary_case(
        candidates=[
            _candidate("已验证事实", importance=0.6, topics=["偏好"]),
        ],
        gate_actions=["allowed"],
        actual_count=22,
        last_summarized_index=20,
    )

    results = await _run_summary(handler, event)

    feedback = results[-1]
    assert "记忆总结完成，已写入 1 条长期记忆" in feedback
    assert "第 22 条消息" in feedback
    assert "第 1 条消息" not in feedback
    engine.add_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_canonical_write_failure_keeps_pending_after_quarantine() -> None:
    """隔离成功不能掩盖 canonical 写入失败或错误推进窗口。"""
    handler, event, conversation_manager, engine = _build_summary_case(
        candidates=[
            _candidate("待复核事实", importance=0.2, topics=["隔离主题"]),
            _candidate("写入失败事实", importance=0.9, topics=["有效主题"]),
        ],
        gate_actions=["quarantined", "allowed"],
        actual_count=18,
        last_summarized_index=14,
        add_side_effect=RuntimeError("模拟 canonical 写入失败"),
    )

    results = await _run_summary(handler, event)

    assert "记忆总结完成" not in results[-1]
    assert "未写入长期记忆" not in results[-1]
    engine.add_memory.assert_awaited_once()
    conversation_manager.update_session_metadata.assert_any_await(
        "session-feedback",
        "pending_summary",
        {
            "start_index": 14,
            "end_index": 18,
            "retry_count": 1,
            "failed_stage": "manual_memory_write",
            "failed_count": 1,
        },
    )
    assert (
        call("session-feedback", "last_summarized_index", 18)
        not in conversation_manager.update_session_metadata.await_args_list
    )
