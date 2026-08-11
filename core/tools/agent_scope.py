"""从 Agent 事件提取受信任的读取作用域。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot.api.platform import MessageType
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

_RESOLVED_IDENTITY_EVENT_EXTRA = "memora.resolved_identity"


@dataclass(frozen=True, slots=True)
class AgentReadScope:
    """表示 Agent 调用可用于读取隔离数据的当前事件作用域。"""

    session_id: str
    user_id: str | None
    chat_type: str


def resolve_agent_read_scope(
    context: ContextWrapper[AstrAgentContext],
    *,
    require_user_id: bool = True,
) -> AgentReadScope | None:
    """从当前事件提取会话、发送者和聊天类型；按调用方要求校验用户标识。"""

    event = getattr(getattr(context, "context", None), "event", None)
    session_id = _normalized_text(getattr(event, "unified_msg_origin", None))
    user_id = _event_user_id(event)
    chat_type = _event_chat_type(event)
    if not session_id or chat_type is None or (require_user_id and not user_id):
        return None
    if chat_type == "group" and not user_id:
        return None
    return AgentReadScope(
        session_id=session_id,
        user_id=user_id,
        chat_type=chat_type,
    )


def _event_user_id(event: Any) -> str | None:
    """读取主链发布的 canonical 用户标识；缺失快照时拒绝读取。"""

    try:
        identity = event.get_extra(_RESOLVED_IDENTITY_EVENT_EXTRA)
    except Exception:
        return None
    trust_status = getattr(identity, "trust_status", None)
    trust_value = getattr(trust_status, "value", trust_status)
    if trust_value == "trusted":
        return _normalized_text(getattr(identity, "canonical_user_id", None))
    if trust_value != "unsupported":
        return None

    return _raw_event_sender_id(event)


def _raw_event_sender_id(event: Any) -> str | None:
    """仅为未接管协议读取原始发送者 ID。"""

    getter = getattr(event, "get_sender_id", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return _normalized_text(value)


def _event_chat_type(event: Any) -> str | None:
    """依据明确的平台消息类型识别群聊或私聊，未知类型拒绝读取。"""

    getter = getattr(event, "get_message_type", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    if value == getattr(MessageType, "GROUP_MESSAGE", None):
        return "group"
    private_values = (
        getattr(MessageType, "FRIEND_MESSAGE", None),
        getattr(MessageType, "PRIVATE_MESSAGE", None),
    )
    if any(value == candidate for candidate in private_values if candidate is not None):
        return "private"
    value_name = getattr(value, "name", None)
    value_text = getattr(value, "value", None)
    if value_name in {"FRIEND_MESSAGE", "PRIVATE_MESSAGE"} or value_text in {
        "FriendMessage",
        "PrivateMessage",
        "FRIEND_MESSAGE",
        "PRIVATE_MESSAGE",
    }:
        return "private"
    return None


def _normalized_text(value: Any) -> str | None:
    """把受控标识规整为非空字符串，拒绝非标量输入。"""

    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


__all__ = ["AgentReadScope", "resolve_agent_read_scope"]
