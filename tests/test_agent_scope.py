"""验证 Agent 读取作用域只接受完整、可信的事件字段。"""

from __future__ import annotations

from unittest.mock import MagicMock

from astrbot.api.platform import MessageType

from core.tools.agent_scope import resolve_agent_read_scope


def _context(*, message_type, sender_id="user-1") -> MagicMock:
    """构造最小 Agent 上下文。"""

    event = MagicMock()
    event.unified_msg_origin = "private:user-1"
    event.get_message_type.return_value = message_type
    event.get_sender_id.return_value = sender_id
    wrapper = MagicMock()
    wrapper.context.event = event
    return wrapper


def test_unknown_message_type_is_denied() -> None:
    """未知消息类型不能默认降级为 private。"""

    assert resolve_agent_read_scope(_context(message_type=object())) is None


def test_group_without_sender_is_denied() -> None:
    """群聊缺少发送者标识时必须 fail-closed。"""

    assert (
        resolve_agent_read_scope(
            _context(message_type=MessageType.GROUP_MESSAGE, sender_id=None)
        )
        is None
    )
