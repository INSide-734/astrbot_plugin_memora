"""OneBot 11 稳定用户身份的事件到记忆参与者集成回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.identity.conversation_sync import ConversationIdentitySynchronizer
from core.identity.memory import MemoryIdentityEnricher, build_memory_identity_context
from core.identity.resolver import ProtocolIdentityResolver
from core.identity.runtime import ProtocolIdentityRuntime
from core.identity.service import ProtocolIdentityService
from core.managers.conversation_manager import ConversationManager
from core.storage.conversation_store import ConversationStore
from core.storage.protocol_identity_store import ProtocolIdentityStore


def _onebot_group_event(*, card: str, timestamp: int) -> MagicMock:
    """构造发送者 QQ 固定、群名片可变化的严格 OneBot 11 群事件。"""

    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:group:20001"
    event.get_platform_name.return_value = "aiocqhttp"
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_sender_id.return_value = "框架显示名不作为身份"
    event.get_sender_name.return_value = card
    event.get_self_id.return_value = "90001"
    event.message_obj = SimpleNamespace(
        self_id="90001",
        sender=SimpleNamespace(user_id="10001"),
        raw_message={
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "time": timestamp,
            "group_id": 20001,
            "user_id": 10001,
            "sender": {
                "user_id": 10001,
                "nickname": "协议昵称",
                "card": card,
            },
        },
    )
    return event


@pytest.mark.asyncio
@pytest.mark.integration
async def test_onebot_rename_keeps_qq_identity_and_updates_memory_name(
    tmp_path: Path,
) -> None:
    """改名后历史消息应同步当前名称，而记忆参与者始终锚定同一 QQ。"""

    conversation_store = ConversationStore(str(tmp_path / "conversations.db"))
    identity_store = ProtocolIdentityStore(str(tmp_path / "memora.db"))
    await conversation_store.initialize()
    await identity_store.initialize()
    manager = ConversationManager(conversation_store)
    service = ProtocolIdentityService(identity_store)
    synchronizer = ConversationIdentitySynchronizer(
        conversation_store,
        service,
        manager.invalidate_cache,
    )
    runtime = ProtocolIdentityRuntime(
        ProtocolIdentityResolver.default(),
        service=service,
        synchronizer=synchronizer,
        store=identity_store,
        enricher=MemoryIdentityEnricher(identity_store),
    )
    manager.identity_runtime = runtime

    try:
        for index, card in enumerate(("旧名称", "新名称"), start=1):
            event = _onebot_group_event(card=card, timestamp=index * 100)
            identity = await runtime.prepare(event)
            message = await manager.add_message_from_event(
                event,
                role="user",
                content=f"第 {index} 条消息",
                identity=identity,
            )
            assert message is not None

        messages = await manager.get_messages(
            "aiocqhttp:group:20001",
            limit=10,
            use_cache=False,
        )
        memory_identity = build_memory_identity_context(messages)

        assert [message.sender_id for message in messages] == ["10001", "10001"]
        assert [message.sender_name for message in messages] == ["新名称", "新名称"]
        assert memory_identity.participant_ids == ("10001",)
        assert memory_identity.participant_labels == ("QQ:10001",)
        assert memory_identity.participant_name_snapshots == {"10001": "新名称"}
        assert await identity_store.find_aliases(
            "qq",
            "10001",
            "group",
            "20001",
        ) == ["旧名称"]
    finally:
        await runtime.close()
        await conversation_store.close()
