"""会话显示名称按协议身份和作用域同步的契约测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.identity import IdentityTrust, NameFieldState, ResolvedIdentity
from core.identity.conversation_sync import ConversationIdentitySynchronizer
from core.identity.service import ProtocolIdentityService
from core.models.conversation_models import Message
from core.storage.conversation_store import ConversationStore
from core.storage.protocol_identity_store import ProtocolIdentityStore


def _identity(
    *,
    scope_type: str = "group",
    scope_id: str = "20001",
    global_name: str = "新昵称",
    scope_name: str | None = "新群名片",
    observed_at: float = 200.0,
    trust_status: IdentityTrust = IdentityTrust.TRUSTED,
) -> ResolvedIdentity:
    """构造同步测试使用的 OneBot 身份快照。"""

    trusted = trust_status is IdentityTrust.TRUSTED
    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id="10001" if trusted else None,
        canonical_user_id="10001" if trusted else None,
        scope_type=scope_type,
        scope_id=scope_id,
        global_name=global_name,
        scope_name=scope_name,
        display_name=scope_name or global_name,
        observed_at=observed_at,
        trust_status=trust_status,
        name_field_states={
            "nickname": NameFieldState.VALID,
            "card": (
                NameFieldState.VALID
                if scope_type == "group" and scope_name
                else NameFieldState.MISSING
            ),
        },
        conversation_sender_id="10001" if trusted else None,
        identity_label="QQ:10001" if trusted else None,
    )


def _qq_official_identity() -> ResolvedIdentity:
    """构造 QQ 官方 C2C 改名同步使用的可信 OpenID 身份。"""

    namespace = "qq-official:instance-key"
    canonical = f"{namespace}:OPENID-1"
    return ResolvedIdentity(
        protocol="qq_official",
        identity_namespace=namespace,
        stable_user_id="OPENID-1",
        canonical_user_id=canonical,
        scope_type="private",
        scope_id=canonical,
        global_name="官方新昵称",
        scope_name=None,
        display_name="官方新昵称",
        observed_at=200.0,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={
            "nickname": NameFieldState.VALID,
            "card": NameFieldState.MISSING,
        },
        conversation_sender_id=canonical,
        identity_label="QQ官方:instance-key:OPENID-1",
    )


def _message(
    *,
    session_id: str,
    sender_id: str = "10001",
    sender_name: str | None = "旧名称",
    role: str = "user",
    group_id: str | None = "20001",
    platform: str = "aiocqhttp",
    timestamp: float = 100.0,
    content: str = "原始正文",
) -> Message:
    """构造可验证非名称字段保持不变的会话消息。"""

    return Message(
        id=0,
        session_id=session_id,
        role=role,
        content=content,
        sender_id=sender_id,
        sender_name=sender_name,
        group_id=group_id,
        platform=platform,
        timestamp=timestamp,
        metadata={"保留": True},
    )


async def _build_sync(tmp_path):
    """创建彼此独立的身份目录、会话库和同步器。"""

    identity_store = ProtocolIdentityStore(str(tmp_path / "memora.db"))
    conversation_store = ConversationStore(str(tmp_path / "conversations.db"))
    await identity_store.initialize()
    await conversation_store.initialize()
    invalidator = AsyncMock()
    synchronizer = ConversationIdentitySynchronizer(
        conversation_store,
        ProtocolIdentityService(identity_store),
        invalidator,
    )
    return synchronizer, identity_store, conversation_store, invalidator


@pytest.mark.asyncio
async def test_group_sync_updates_only_current_session(tmp_path) -> None:
    """群聊同步只修改当前 session 中同 QQ 的用户消息。"""

    sync, identity_store, conversations, invalidator = await _build_sync(tmp_path)
    try:
        await conversations.add_message(
            _message(session_id="group-a", sender_name="旧群名片A")
        )
        await conversations.add_message(
            _message(session_id="group-b", sender_name="旧群名片B")
        )
        await conversations.add_message(
            _message(session_id="group-a", sender_id="10002", sender_name="另一用户")
        )
        await conversations.add_message(
            _message(
                session_id="group-a",
                sender_id="bot",
                sender_name="机器人",
                role="assistant",
            )
        )

        changed = await sync.synchronize(_identity(), session_id="group-a")

        assert changed == {"group-a"}
        group_a = await conversations.get_messages("group-a", limit=10)
        group_b = await conversations.get_messages("group-b", limit=10)
        target = next(message for message in group_a if message.sender_id == "10001")
        other = next(message for message in group_a if message.sender_id == "10002")
        assistant = next(message for message in group_a if message.role == "assistant")
        assert target.sender_name == "新群名片"
        assert target.content == "原始正文"
        assert target.sender_id == "10001"
        assert target.role == "user"
        assert target.timestamp == 100.0
        assert target.metadata == {"保留": True}
        assert other.sender_name == "另一用户"
        assert assistant.sender_name == "机器人"
        assert group_b[0].sender_name == "旧群名片B"
        assert await identity_store.find_aliases("qq", "10001", "group", "20001") == [
            "旧群名片A"
        ]
        invalidator.assert_awaited_once_with("group-a")
    finally:
        await conversations.close()
        await identity_store.close()


@pytest.mark.asyncio
async def test_private_sync_updates_only_proven_onebot_private_sessions(
    tmp_path,
) -> None:
    """OneBot 私聊同步不能跨到其他平台或群聊。"""

    sync, identity_store, conversations, invalidator = await _build_sync(tmp_path)
    try:
        await conversations.add_message(
            _message(session_id="private-a", sender_name="旧昵称A", group_id=None)
        )
        await conversations.add_message(
            _message(session_id="private-b", sender_name="旧昵称B", group_id=None)
        )
        await conversations.add_message(
            _message(
                session_id="telegram-private",
                sender_name="Telegram 名称",
                group_id=None,
                platform="telegram",
            )
        )
        await conversations.add_message(
            _message(session_id="onebot-group", sender_name="群名片", group_id="20001")
        )
        identity = _identity(
            scope_type="private",
            scope_id="10001",
            scope_name=None,
        )

        changed = await sync.synchronize(identity, session_id="private-a")

        assert changed == {"private-a", "private-b"}
        assert (await conversations.get_messages("private-a", 10))[
            0
        ].sender_name == "新昵称"
        assert (await conversations.get_messages("private-b", 10))[
            0
        ].sender_name == "新昵称"
        assert (await conversations.get_messages("telegram-private", 10))[
            0
        ].sender_name == "Telegram 名称"
        assert (await conversations.get_messages("onebot-group", 10))[
            0
        ].sender_name == "群名片"
        assert set(await identity_store.find_aliases("qq", "10001", "global", "")) == {
            "旧昵称A",
            "旧昵称B",
        }
        assert {call.args[0] for call in invalidator.await_args_list} == changed
    finally:
        await conversations.close()
        await identity_store.close()


@pytest.mark.asyncio
async def test_qq_official_private_sync_updates_only_current_session(tmp_path) -> None:
    """QQ 官方私聊改名只同步当前平台实例的完整会话。"""

    sync, identity_store, conversations, invalidator = await _build_sync(tmp_path)
    canonical = "qq-official:instance-key:OPENID-1"
    try:
        await conversations.add_message(
            _message(
                session_id="qq_official:FriendMessage:OPENID-1",
                sender_id=canonical,
                sender_name="官方旧昵称",
                group_id=None,
                platform="qq_official",
            )
        )
        await conversations.add_message(
            _message(
                session_id="qq_official_webhook:FriendMessage:OPENID-1",
                sender_id=canonical,
                sender_name="另一实例旧昵称",
                group_id=None,
                platform="qq_official_webhook",
            )
        )

        changed = await sync.synchronize(
            _qq_official_identity(),
            session_id="qq_official:FriendMessage:OPENID-1",
        )

        assert changed == {"qq_official:FriendMessage:OPENID-1"}
        current = await conversations.get_messages(
            "qq_official:FriendMessage:OPENID-1", 10
        )
        other = await conversations.get_messages(
            "qq_official_webhook:FriendMessage:OPENID-1", 10
        )
        assert current[0].sender_id == canonical
        assert current[0].sender_name == "官方新昵称"
        assert current[0].content == "原始正文"
        assert other[0].sender_name == "另一实例旧昵称"
        assert await identity_store.find_aliases(
            "qq-official:instance-key",
            "OPENID-1",
            "global",
            "",
        ) == ["官方旧昵称"]
        invalidator.assert_awaited_once_with("qq_official:FriendMessage:OPENID-1")
    finally:
        await conversations.close()
        await identity_store.close()


@pytest.mark.asyncio
async def test_admin_display_name_remains_highest_priority_during_sync(
    tmp_path,
) -> None:
    """会话同步应采用管理员备注，但不得修改画像表。"""

    sync, identity_store, conversations, _invalidator = await _build_sync(tmp_path)
    try:
        async with aiosqlite.connect(identity_store.db_path) as connection:
            await connection.execute(
                "CREATE TABLE user_profiles (user_id TEXT PRIMARY KEY, display_name TEXT)"
            )
            await connection.execute(
                "INSERT INTO user_profiles(user_id, display_name) VALUES (?, ?)",
                ("10001", "管理员备注"),
            )
            await connection.commit()
        await conversations.add_message(_message(session_id="group-a"))

        await sync.synchronize(_identity(), session_id="group-a")

        message = (await conversations.get_messages("group-a", 10))[0]
        assert message.sender_name == "管理员备注"
        async with aiosqlite.connect(identity_store.db_path) as connection:
            cursor = await connection.execute(
                "SELECT display_name FROM user_profiles WHERE user_id = ?", ("10001",)
            )
            assert (await cursor.fetchone())[0] == "管理员备注"
    finally:
        await conversations.close()
        await identity_store.close()


@pytest.mark.asyncio
async def test_untrusted_identity_does_not_touch_conversation(tmp_path) -> None:
    """冲突身份不得写目录、改名称或失效会话缓存。"""

    sync, identity_store, conversations, invalidator = await _build_sync(tmp_path)
    try:
        await conversations.add_message(_message(session_id="group-a"))

        changed = await sync.synchronize(
            _identity(trust_status=IdentityTrust.CONFLICT),
            session_id="group-a",
        )

        assert changed == set()
        assert (await conversations.get_messages("group-a", 10))[
            0
        ].sender_name == "旧名称"
        assert (
            await identity_store.get_identity("qq", "10001", "group", "20001") is None
        )
        invalidator.assert_not_awaited()
    finally:
        await conversations.close()
        await identity_store.close()
