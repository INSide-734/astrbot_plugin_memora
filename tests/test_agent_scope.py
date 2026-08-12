"""验证 Agent 读取作用域只接受完整、可信的事件字段。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

from astrbot.api.platform import MessageType

from core.features.identity.infrastructure.protocols import ProtocolIdentityResolver
from core.tools.agent_scope import resolve_agent_read_scope


def _event(
    *,
    message_type,
    sender_id: str | None = "user-1",
    with_identity: bool = True,
) -> MagicMock:
    """构造公开工具读取作用域所需的最小事件。"""

    event = MagicMock()
    event.unified_msg_origin = "private:user-1"
    event.get_message_type.return_value = message_type
    event.get_sender_id.return_value = sender_id
    if with_identity:
        event.get_extra.return_value = SimpleNamespace(trust_status="unsupported")
    else:
        event.get_extra.return_value = None
    return event


def _qq_official_event(*, sender_id: str = "OPENID-1") -> MagicMock:
    """构造 QQ Official C2C Agent 事件。"""

    platform_id = "official-bot-1"
    author = {"id": "OPENID-1", "user_openid": "OPENID-1"}
    event = MagicMock()
    event.unified_msg_origin = "qq-official:c2c:OPENID-1"
    event.message_obj = SimpleNamespace(
        raw_message=SimpleNamespace(
            raw_data={"author": author},
            author=SimpleNamespace(user_openid="OPENID-1"),
        ),
        sender=SimpleNamespace(user_id=sender_id),
        group_id=None,
    )
    event.get_platform_name.return_value = "qq_official"
    event.get_platform_id.return_value = platform_id
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
    event.get_sender_id.return_value = sender_id
    event.get_extra.return_value = ProtocolIdentityResolver.default().resolve(event)
    instance_key = hashlib.sha256(platform_id.encode("ascii")).hexdigest()[:24]
    event.expected_canonical_user_id = f"qq-official:{instance_key}:OPENID-1"
    return event


def test_unknown_message_type_is_denied() -> None:
    """未知消息类型不能默认降级为 private。"""

    assert resolve_agent_read_scope(_event(message_type=object())) is None


def test_missing_resolved_identity_is_denied() -> None:
    """有效消息缺少主链解析快照时必须 fail-closed。"""

    assert (
        resolve_agent_read_scope(
            _event(
                message_type=MessageType.FRIEND_MESSAGE,
                with_identity=False,
            )
        )
        is None
    )


def test_resolved_identity_read_failure_is_denied() -> None:
    """事件 extras 读取失败时必须 fail-closed。"""

    event = _event(message_type=MessageType.FRIEND_MESSAGE)
    event.get_extra.side_effect = RuntimeError("private")

    assert resolve_agent_read_scope(event) is None


def test_group_without_sender_is_denied() -> None:
    """群聊缺少发送者标识时必须 fail-closed。"""

    assert (
        resolve_agent_read_scope(
            _event(message_type=MessageType.GROUP_MESSAGE, sender_id=None)
        )
        is None
    )


def test_qq_official_scope_uses_canonical_user_id() -> None:
    """Agent 读取作用域必须使用带平台实例命名空间的 canonical ID。"""

    event = _qq_official_event()

    scope = resolve_agent_read_scope(event)

    assert scope is not None
    event.get_extra.assert_called_once_with("memora.resolved_identity")
    assert scope.user_id == event.expected_canonical_user_id


def test_qq_official_conflicting_identity_is_denied() -> None:
    """QQ Official 身份证据冲突时 Agent 读取必须 fail-closed。"""

    assert (
        resolve_agent_read_scope(_qq_official_event(sender_id="OTHER-OPENID")) is None
    )
