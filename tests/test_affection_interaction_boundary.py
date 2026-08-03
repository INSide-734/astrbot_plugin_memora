"""好感度自动交互的身份边界与失败日志回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.affection.affection_manager import AffectionManager
from core.affection.affection_store import AffectionStore
from core.affection.models import BotMood, MoodType


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "group_id", "invalid_identity"),
    [
        ("user", "g" * 129, "g" * 129),
        ("u" * 129, "group", "u" * 129),
    ],
)
async def test_invalid_identity_is_rejected_before_any_write(
    tmp_db_path: str,
    user_id: str,
    group_id: str,
    invalid_identity: str,
) -> None:
    """超长身份必须无副作用地失败，且日志不得包含原始身份。"""
    store = AffectionStore(tmp_db_path)
    await store.initialize()
    try:
        manager = AffectionManager(store)
        with patch("core.affection.affection_manager.logger") as logger:
            result = await manager.process_interaction(
                user_id,
                group_id,
                "你好棒",
                "谢谢",
            )

        assert result["success"] is False
        assert result["error"] == "身份无效"
        assert await store.get_affection(group_id, user_id) is None
        assert await store.get_latest_mood(group_id) is None
        assert invalid_identity not in repr(logger.method_calls)
        logger.exception.assert_not_called()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_identity_is_normalized_before_persistence(tmp_db_path: str) -> None:
    """自动交互应与管理员接口一致，在写入前去除身份两端空白。"""
    store = AffectionStore(tmp_db_path)
    await store.initialize()
    try:
        manager = AffectionManager(store)
        result = await manager.process_interaction(
            " user ",
            " group ",
            "你好棒",
            "谢谢",
        )

        assert result["success"] is True
        assert await store.get_affection("group", "user") is not None
        assert await store.get_affection(" group ", " user ") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_internal_failure_log_does_not_include_identity(tmp_db_path: str) -> None:
    """普通内部失败可以保留堆栈，但不得把关系身份写入日志。"""
    store = AffectionStore(tmp_db_path)
    await store.initialize()
    try:
        manager = AffectionManager(store)
        mood = BotMood(mood_type=MoodType.CALM)
        manager._ensure_mood = AsyncMock(return_value=mood)
        store.upsert_affection = AsyncMock(side_effect=RuntimeError("测试故障"))

        with patch("core.affection.affection_manager.logger") as logger:
            result = await manager.process_interaction(
                "private-user",
                "private-group",
                "你好棒",
                "谢谢",
            )

        assert result["success"] is False
        assert "private-user" not in repr(logger.method_calls)
        assert "private-group" not in repr(logger.method_calls)
        logger.exception.assert_called_once_with(
            "[好感度管理] process_interaction 失败"
        )
    finally:
        await store.close()
