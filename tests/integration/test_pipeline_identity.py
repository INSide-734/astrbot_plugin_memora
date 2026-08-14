"""OneBot 11 稳定用户身份的事件到记忆参与者集成回归。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.features.conversation.application.conversation_manager import (
    ConversationManager,
)
from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.identity.application.conversation_sync import (
    ConversationIdentitySynchronizer,
)
from core.features.identity.application.enricher import (
    MemoryIdentityEnricher,
    build_memory_identity_context,
)
from core.features.identity.application.runtime import ProtocolIdentityRuntime
from core.features.identity.application.service import ProtocolIdentityService
from core.features.identity.infrastructure.protocols import ProtocolIdentityResolver
from core.features.identity.infrastructure.store import ProtocolIdentityStore


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


def _qq_official_group_event(*, username: str, timestamp: str) -> SimpleNamespace:
    """构造 OpenID 固定、协议名称可变化的 QQ 官方群事件。"""

    openid = "00A1B2C3D4E5F60718293A4B5C6D7E8F"
    group_openid = "GROUP-OPENID-1"
    raw_data = {
        "author": {
            "id": openid,
            "member_openid": openid,
            "username": username,
        },
        "group_openid": group_openid,
        "timestamp": timestamp,
    }
    message_obj = SimpleNamespace(
        self_id="official-bot",
        sender=SimpleNamespace(user_id=openid, nickname=username),
        group_id=group_openid,
        raw_message=SimpleNamespace(
            raw_data=raw_data,
            author=SimpleNamespace(member_openid=openid),
            group_openid=group_openid,
            timestamp=timestamp,
        ),
    )
    return SimpleNamespace(
        unified_msg_origin="qq_official:GroupMessage:GROUP-OPENID-1",
        message_obj=message_obj,
        get_platform_name=lambda: "qq_official",
        get_platform_id=lambda: "official-bot-1",
        get_message_type=lambda: MessageType.GROUP_MESSAGE,
        get_sender_id=lambda: openid,
        get_sender_name=lambda: username,
        get_group_id=lambda: group_openid,
        get_self_id=lambda: "official-bot",
    )


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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_qq_official_rename_keeps_openid_and_updates_memory_prompt(
    tmp_path: Path,
) -> None:
    """QQ 官方改名后应保留 namespaced OpenID 并更新记忆名称约束。"""

    conversation_store = ConversationStore(str(tmp_path / "official-conversations.db"))
    identity_store = ProtocolIdentityStore(str(tmp_path / "official-memora.db"))
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

    instance_key = hashlib.sha256(b"official-bot-1").hexdigest()[:24]
    namespace = f"qq-official:{instance_key}"
    openid = "00A1B2C3D4E5F60718293A4B5C6D7E8F"
    canonical = f"{namespace}:{openid}"
    label = f"QQ官方:{instance_key}:{openid}"
    try:
        observations = (
            ("官方旧名称", "2026-07-23T12:00:00+08:00"),
            ("官方新名称", "2026-07-23T12:01:00+08:00"),
        )
        for index, (username, timestamp) in enumerate(observations, start=1):
            event = _qq_official_group_event(
                username=username,
                timestamp=timestamp,
            )
            identity = await runtime.prepare(event)
            message = await manager.add_message_from_event(
                event,
                role="user",
                content=f"官方第 {index} 条消息",
                identity=identity,
            )
            assert message is not None

        messages = await manager.get_messages(
            "qq_official:GroupMessage:GROUP-OPENID-1",
            limit=10,
            use_cache=False,
        )
        memory_identity = build_memory_identity_context(messages)
        metadata = memory_identity.metadata()

        assert [message.sender_id for message in messages] == [canonical, canonical]
        assert [message.sender_name for message in messages] == [
            "官方新名称",
            "官方新名称",
        ]
        assert memory_identity.participant_ids == (canonical,)
        assert memory_identity.participant_labels == (label,)
        assert memory_identity.participant_name_snapshots == {canonical: "官方新名称"}
        assert metadata["participant_identity_sources"][canonical] == {
            "protocol": "qq_official",
            "identity_namespace": namespace,
            "stable_user_id": openid,
            "identity_label": label,
        }
        assert f"官方新名称（{label}）" in memory_identity.prompt_constraint()
        assert await identity_store.find_aliases(
            namespace,
            openid,
            "global",
            "",
        ) == ["官方旧名称"]
    finally:
        await runtime.close()
        await conversation_store.close()
