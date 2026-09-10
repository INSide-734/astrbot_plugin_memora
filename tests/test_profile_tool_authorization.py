"""验证 Profile Agent 工具只读取可信授权目标。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.platform.transport.tools.profile_tools import ProfileLookupTool
from tests.tool_contract_support import call_text_handler


def _context(sender_id: str | None) -> MagicMock:
    """构造只暴露当前发送者身份的公开工具事件替身。"""

    event = MagicMock()
    if sender_id is None:
        del event.get_sender_id
    else:
        event.get_sender_id.return_value = sender_id
    return event


@pytest.mark.asyncio
async def test_explicit_cross_user_lookup_fails_closed() -> None:
    """模型参数不能把当前发送者的权限扩大到其他用户。"""

    manager = MagicMock()
    manager.get_profile = AsyncMock()
    tool = ProfileLookupTool(profile_manager=manager)

    result = await call_text_handler(
        tool,
        _context("current-user"),
        user_id="other-user",
    )

    payload = json.loads(result)
    assert payload == {"found": False, "error": "profile_scope_denied"}
    manager.get_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_trusted_sender_does_not_use_model_target() -> None:
    """无法取得可信事件身份时不得回退模型参数或会话 ID。"""

    manager = MagicMock()
    manager.get_profile = AsyncMock()
    context = _context(None)
    context.unified_msg_origin = "session-is-not-a-user"
    tool = ProfileLookupTool(profile_manager=manager)

    result = await call_text_handler(tool, context, user_id="claimed-user")

    payload = json.loads(result)
    assert payload == {"found": False, "error": "trusted_identity_unavailable"}
    manager.get_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_self_lookup_uses_trusted_sender() -> None:
    """显式目标与当前发送者一致时保持原有查询能力。"""

    profile = MagicMock()
    profile.user_id = "current-user"
    profile.display_name = "匿名用户"
    profile.tags = []
    profile.preferences = MagicMock(
        reply_style="casual",
        preferred_topics=[],
        avoided_topics=[],
        avg_reply_length=0,
    )
    profile.total_messages = 0
    profile.total_sessions = 0
    manager = MagicMock()
    manager.get_profile = AsyncMock(return_value=profile)
    manager.get_tag_weights = AsyncMock(return_value={})
    tool = ProfileLookupTool(profile_manager=manager)

    result = await call_text_handler(
        tool,
        _context("current-user"),
        user_id="current-user",
    )

    assert json.loads(result)["found"] is True
    manager.get_profile.assert_awaited_once_with("current-user")


@pytest.mark.asyncio
async def test_authorization_checker_can_allow_explicit_target() -> None:
    """显式授权检查器可以允许管理员式跨用户读取。"""

    manager = MagicMock()
    manager.get_profile = AsyncMock(return_value=None)
    checker = AsyncMock(return_value=True)
    tool = ProfileLookupTool(
        profile_manager=manager,
        authorization_checker=checker,
    )
    context = _context("admin-user")

    result = await call_text_handler(tool, context, user_id="target-user")

    assert json.loads(result) == {"user_id": "target-user", "found": False}
    checker.assert_awaited_once_with(
        context,
        "admin-user",
        "target-user",
    )


@pytest.mark.asyncio
async def test_authorization_cancellation_propagates() -> None:
    """关闭时的取消异常不能被授权降级吞掉。"""

    checker = AsyncMock(side_effect=asyncio.CancelledError)
    tool = ProfileLookupTool(
        profile_manager=MagicMock(),
        authorization_checker=checker,
    )

    with pytest.raises(asyncio.CancelledError):
        await call_text_handler(
            tool,
            _context("admin-user"),
            user_id="target-user",
        )
