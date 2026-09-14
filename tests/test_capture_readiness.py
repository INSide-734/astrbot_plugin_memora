"""早期捕获就绪（F5c）的取消、去重与关停行为回归。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.features.identity.domain.models import IdentityTrust, ResolvedIdentity
from core.platform.composition.readiness import CaptureRuntime


def _identity(trust: IdentityTrust = IdentityTrust.TRUSTED) -> ResolvedIdentity:
    """构造只供捕获边界使用的最小可信身份快照。"""

    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id="10001",
        canonical_user_id="10001",
        scope_type="private",
        scope_id="10001",
        global_name="昵称甲",
        scope_name=None,
        display_name="昵称甲",
        observed_at=100.0,
        trust_status=trust,
        name_field_states={},
        conversation_sender_id="10001",
        identity_label="QQ:10001",
    )


def _event(
    message_type: MessageType = MessageType.FRIEND_MESSAGE,
    *,
    sender: str = "10001",
    self_id: str = "99999",
    message_id: str = "m-1",
) -> SimpleNamespace:
    """构造满足捕获边界窄接口的最小事件。"""

    return SimpleNamespace(
        get_message_type=lambda: message_type,
        get_sender_id=lambda: sender,
        get_self_id=lambda: self_id,
        unified_msg_origin="aiocqhttp:private:10001",
        message_obj=SimpleNamespace(message_id=message_id, timestamp=100.0),
    )


def _conversation_manager() -> SimpleNamespace:
    """构造只记录持久化调用的共享会话管理器替身。"""

    return SimpleNamespace(
        add_message_from_event=AsyncMock(return_value=SimpleNamespace(id=1))
    )


def _runtime(
    manager: SimpleNamespace,
    *,
    identity: ResolvedIdentity | None = None,
    blocked: bool = False,
) -> CaptureRuntime:
    """构造写保护可切换的早期捕获运行时。"""

    identity_runtime = SimpleNamespace(
        prepare=AsyncMock(return_value=identity or _identity())
    )
    return CaptureRuntime(
        manager,
        identity_runtime,
        MagicMock(),
        lambda: blocked,
    )


@pytest.mark.asyncio
async def test_capture_persists_user_message_and_marks_event() -> None:
    """已就绪依赖下的用户消息写入共享会话并标记去重。"""

    manager = _conversation_manager()
    runtime = _runtime(manager)
    event = _event()

    assert await runtime.capture_event(event, content="用户喜欢咖啡") is True

    manager.add_message_from_event.assert_awaited_once()
    call = manager.add_message_from_event.await_args
    assert call.kwargs["role"] == "user"
    assert call.kwargs["content"] == "用户喜欢咖啡"
    assert getattr(event, "_memora_early_capture_persisted") is True


@pytest.mark.asyncio
async def test_capture_skips_duplicate_event_before_persisting() -> None:
    """同一事件的二次捕获不得重复写入共享会话。"""

    manager = _conversation_manager()
    runtime = _runtime(manager)
    event = _event()

    assert await runtime.capture_event(event, content="重复内容") is True
    assert await runtime.capture_event(event, content="重复内容") is False

    manager.add_message_from_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_respects_disabled_full_group_capture() -> None:
    """全群捕获关闭时，初始化早期路径也不得写入群消息。"""
    manager = _conversation_manager()
    runtime = _runtime(manager)
    runtime.config_manager.get.return_value = False

    assert (
        await runtime.capture_event(_event(MessageType.GROUP_MESSAGE), content="群消息")
        is False
    )
    manager.add_message_from_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_rejects_when_write_guard_blocks() -> None:
    """维护写保护期间不得写入任何消息。"""

    manager = _conversation_manager()
    runtime = _runtime(manager, blocked=True)

    assert await runtime.capture_event(_event(), content="内容") is False
    manager.add_message_from_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_rejects_conflict_identity_and_self_message() -> None:
    """冲突身份与自身消息都不得进入共享会话。"""

    manager = _conversation_manager()
    conflicted = _runtime(manager, identity=_identity(IdentityTrust.CONFLICT))
    assert await conflicted.capture_event(_event(), content="内容") is False

    self_event = _event(sender="99999", self_id="99999")
    trusted = _runtime(manager)
    assert await trusted.capture_event(self_event, content="内容") is False

    manager.add_message_from_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_propagates_cancellation() -> None:
    """捕获过程中的取消必须继续传播，不得降级为普通 False。"""

    manager = _conversation_manager()
    manager.add_message_from_event = AsyncMock(side_effect=asyncio.CancelledError)
    runtime = _runtime(manager)

    with pytest.raises(asyncio.CancelledError):
        await runtime.capture_event(_event(), content="内容")


@pytest.mark.asyncio
async def test_close_rejects_new_events_and_drains_inflight() -> None:
    """关停后拒绝新事件，并等待在途捕获完整收束。"""

    release = asyncio.Event()
    started = asyncio.Event()

    async def _blocked_add(**_kwargs: object) -> SimpleNamespace:
        started.set()
        await release.wait()
        return SimpleNamespace(id=1)

    manager = _conversation_manager()
    manager.add_message_from_event = AsyncMock(side_effect=_blocked_add)
    runtime = _runtime(manager)

    inflight = asyncio.create_task(runtime.capture_event(_event(), content="在途"))
    await asyncio.wait_for(started.wait(), timeout=1)
    closing = asyncio.create_task(runtime.close())

    await asyncio.sleep(0)
    assert closing.done() is False

    release.set()
    assert await asyncio.wait_for(inflight, timeout=1) is True
    await asyncio.wait_for(closing, timeout=1)

    assert await runtime.capture_event(_event(message_id="m-2"), content="新") is False
    manager.add_message_from_event.assert_awaited_once()
