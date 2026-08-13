"""Page stats 使用真实 ConversationStore 会话的契约测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.shared.contracts.conversation import Session
from core.platform.transport.page_api.memory_stats_recall_api import (
    MemoryStatsRecallApiMixin,
)


class _StatsHarness:
    """为 ``get_stats`` 提供最小 Page API 宿主。"""

    get_stats = MemoryStatsRecallApiMixin.get_stats

    def __init__(self, memory_engine: MagicMock, conversation_manager) -> None:
        """保存测试需要发布的记忆引擎和会话管理器。"""
        self._memory_engine = memory_engine
        self._conversation_manager = conversation_manager

    async def _ensure_plugin_ready(self):
        """返回与生产 Page API 一致的 ready context。"""
        return {
            "memory_engine": self._memory_engine,
            "conversation_manager": self._conversation_manager,
        }, None

    @staticmethod
    def _get_graph_store(_engine):
        """关闭与当前统计契约无关的图存储。"""
        return None

    @staticmethod
    def _ok(data):
        """构造成功响应。"""
        return {"status": "ok", "data": data}

    @staticmethod
    def _error(message):
        """构造失败响应。"""
        return {"status": "error", "message": message}


def _memory_engine(stats: dict) -> MagicMock:
    """构造返回指定 canonical 统计的记忆引擎替身。"""
    engine = MagicMock()
    engine.get_statistics = AsyncMock(return_value=stats)
    engine.atom_store = None
    return engine


def _session(session_id: str, message_count: int, last_active_at: float) -> Session:
    """构造最近会话查询返回的领域对象。"""
    return Session(
        id=1,
        session_id=session_id,
        platform="webchat",
        created_at=last_active_at - 60,
        last_active_at=last_active_at,
        message_count=message_count,
    )


@pytest.mark.asyncio
async def test_stats_uses_real_sessions_when_canonical_is_empty() -> None:
    """canonical 为空时仍应展示 ConversationStore 中的真实活跃会话。"""
    manager = MagicMock()
    manager.get_recent_sessions = AsyncMock(
        return_value=[
            _session("session-live-a", 192, 200.0),
            _session("session-live-b", 18, 100.0),
        ]
    )
    harness = _StatsHarness(
        _memory_engine({"total_memories": 0, "sessions": {}}), manager
    )

    result = await harness.get_stats()

    assert result["status"] == "ok"
    assert result["data"]["sessions"] == {
        "session-live-a": 192,
        "session-live-b": 18,
    }
    assert result["data"]["recent_sessions"] == [
        {"session_id": "session-live-a", "message_count": 192},
        {"session_id": "session-live-b", "message_count": 18},
    ]
    manager.get_recent_sessions.assert_awaited_once_with(limit=10)


@pytest.mark.asyncio
async def test_stats_falls_back_when_conversation_manager_is_missing() -> None:
    """未发布会话管理器时应保留 canonical 聚合兼容行为。"""
    harness = _StatsHarness(
        _memory_engine(
            {
                "sessions": {
                    "canonical-low": 2,
                    "canonical-high": 5,
                }
            }
        ),
        None,
    )

    result = await harness.get_stats()

    assert result["status"] == "ok"
    assert result["data"]["sessions"] == {
        "canonical-low": 2,
        "canonical-high": 5,
    }
    assert result["data"]["recent_sessions"] == [
        {"session_id": "canonical-high", "message_count": 5},
        {"session_id": "canonical-low", "message_count": 2},
    ]


@pytest.mark.asyncio
async def test_stats_falls_back_after_session_lookup_failure() -> None:
    """真实会话普通读取失败时应回退 canonical 聚合而非使端点失败。"""
    manager = MagicMock()
    manager.get_recent_sessions = AsyncMock(side_effect=RuntimeError("模拟读取失败"))
    harness = _StatsHarness(
        _memory_engine({"sessions": {"canonical-session": 3}}), manager
    )

    result = await harness.get_stats()

    assert result["status"] == "ok"
    assert result["data"]["sessions"] == {"canonical-session": 3}
    assert result["data"]["recent_sessions"] == [
        {"session_id": "canonical-session", "message_count": 3},
    ]
    manager.get_recent_sessions.assert_awaited_once_with(limit=10)


@pytest.mark.asyncio
async def test_stats_propagates_session_lookup_cancellation() -> None:
    """真实会话读取收到取消信号时不得回退或伪装成成功。"""
    manager = MagicMock()
    manager.get_recent_sessions = AsyncMock(side_effect=asyncio.CancelledError)
    harness = _StatsHarness(_memory_engine({"sessions": {}}), manager)

    with pytest.raises(asyncio.CancelledError):
        await harness.get_stats()

    manager.get_recent_sessions.assert_awaited_once_with(limit=10)
