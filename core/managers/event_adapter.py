"""
事件适配 Mixin
提供将 AstrBot 平台事件转换为内部消息格式的能力。
"""

from typing import Any

from astrbot.api.platform import MessageType

from ..identity.models import IdentityTrust, ResolvedIdentity
from ..models.conversation_models import Message
from .sender_resolver import _resolve_sender_name


class EventAdapterMixin:
    """将 AstrBot MessageEvent 转换为内部 Message 模型。"""

    async def add_message_from_event(
        self,
        event: Any,  # AstrBot MessageEvent
        role: str,
        content: str,
        identity: ResolvedIdentity | None = None,
    ) -> Message | None:
        """
        从AstrBot事件添加消息(自动提取发送者信息)

        Args:
            event: AstrBot的MessageEvent对象
            role: 消息角色 ("user" 或 "assistant")
            content: 消息内容
            identity: 可选的严格协议身份快照

        Returns:
            创建的 Message；非法或冲突 user 身份返回 None
        """
        # 使用 unified_msg_origin 作为会话ID，确保多Bot场景下的唯一性
        session_id = event.unified_msg_origin

        if (
            role == "user"
            and identity is not None
            and identity.trust_status in {IdentityTrust.CONFLICT, IdentityTrust.INVALID}
        ):
            return None

        # 提取发送者信息
        sender_id = None
        sender_name = None
        group_id = None

        # 尝试获取发送者ID
        if hasattr(event, "get_sender_id"):
            sender_id = event.get_sender_id()
        elif hasattr(event, "sender_id"):
            sender_id = event.sender_id

        # 如果还是没有sender_id,使用session_id作为后备
        if not sender_id:
            sender_id = session_id

        sender_name = _resolve_sender_name(event, sender_id)

        # 判断是否群聊（使用 get_message_type 而非 is_group，更可靠）
        is_group = identity is not None and identity.scope_type == "group"
        if not is_group and hasattr(event, "get_message_type"):
            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
        if is_group:
            group_id = (
                identity.scope_id
                if identity is not None and identity.scope_type == "group"
                else session_id
            )

        is_bot_message = role == "assistant"
        identity_metadata: dict[str, str] = {}
        if is_bot_message:
            bot_id = None
            if hasattr(event, "get_self_id"):
                bot_id = event.get_self_id()
            if not bot_id and hasattr(event, "message_obj"):
                bot_id = getattr(event.message_obj, "self_id", None)
            if bot_id:
                sender_id = str(bot_id)
                sender_name = str(bot_id)
        elif identity is not None and identity.trust_status is IdentityTrust.TRUSTED:
            sender_id = identity.canonical_user_id
            sender_name = identity.display_name or identity.canonical_user_id
            group_id = identity.scope_id if identity.scope_type == "group" else None
            identity_metadata = self._trusted_identity_metadata(identity)
        elif identity is not None and identity.trust_status is IdentityTrust.ANONYMOUS:
            if not identity.conversation_sender_id:
                return None
            sender_id = identity.conversation_sender_id
            sender_name = identity.display_name or "匿名用户"
            group_id = identity.scope_id if identity.scope_type == "group" else None

        # 获取平台名称（字符串）
        platform = (
            event.get_platform_name()
            if hasattr(event, "get_platform_name")
            else "unknown"
        )

        return await self.add_message(
            session_id=session_id,
            role=role,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            platform=platform,
            is_bot_message=(role == "assistant"),
            metadata=identity_metadata,
        )

    @staticmethod
    def _trusted_identity_metadata(identity: ResolvedIdentity) -> dict[str, str]:
        """生成 Message 使用的可信稳定身份 allowlist 元数据。"""

        metadata = {
            "identity_protocol": identity.protocol,
            "identity_namespace": identity.identity_namespace,
            "stable_user_id": identity.stable_user_id or "",
            "canonical_user_id": identity.canonical_user_id or "",
            "identity_label": identity.identity_label or "",
        }
        return {key: value for key, value in metadata.items() if value}
