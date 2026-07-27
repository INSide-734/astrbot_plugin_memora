"""QQ 官方机器人协议稳定身份适配器的行为契约。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from astrbot.api.platform import MessageType

from core.identity import (
    IdentityTrust,
    NameFieldState,
    ProtocolIdentityResolver,
    QQOfficialIdentityAdapter,
)

_OPENID = "00A1B2C3D4E5F60718293A4B5C6D7E8F"
_OTHER_OPENID = "11A1B2C3D4E5F60718293A4B5C6D7E8F"
_PLATFORM_ID = "official-bot-1"
_INSTANCE_KEY = hashlib.sha256(_PLATFORM_ID.encode("utf-8")).hexdigest()[:24]
_NAMESPACE = f"qq-official:{_INSTANCE_KEY}"
_CANONICAL_ID = f"{_NAMESPACE}:{_OPENID}"
_LABEL = f"QQ官方:{_INSTANCE_KEY}:{_OPENID}"


def _qq_official_event(
    *,
    scene: str = "group",
    platform: str = "qq_official",
    platform_id: object = _PLATFORM_ID,
    openid: object = _OPENID,
    author_id: object = _OPENID,
    wrapper_user_id: object = _OPENID,
    username: object = "官方昵称",
    member_nick: object = "频道昵称",
    group_openid: object = "GROUP-OPENID-1",
    guild_id: object = "GUILD-1",
    channel_id: object = "CHANNEL-1",
    wrapper_group_id: object | None = None,
    timestamp: object = "2026-07-23T12:34:56+08:00",
    union_openid: object = None,
) -> SimpleNamespace:
    """构造贴近 AstrBot 4.26.6 patched botpy 对象的官方事件替身。"""

    author: dict[str, object] = {
        "id": author_id,
        "username": username,
        "union_openid": union_openid,
    }
    raw_data: dict[str, object] = {
        "author": author,
        "timestamp": timestamp,
    }
    message_type = MessageType.GROUP_MESSAGE
    raw_author_fields: dict[str, object] = {}

    if scene == "group":
        author["member_openid"] = openid
        raw_author_fields["member_openid"] = openid
        raw_data["group_openid"] = group_openid
        raw_group_id = group_openid
    elif scene == "c2c":
        author["user_openid"] = openid
        raw_author_fields["user_openid"] = openid
        message_type = MessageType.FRIEND_MESSAGE
        raw_group_id = None
    elif scene in {"channel", "direct"}:
        raw_data.update(
            {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "member": {"nick": member_nick},
            }
        )
        raw_author_fields.update({"id": author_id, "username": username})
        if scene == "direct":
            raw_data["direct_message"] = True
            raw_data["src_guild_id"] = guild_id
            message_type = MessageType.FRIEND_MESSAGE
            raw_group_id = None
        else:
            raw_group_id = channel_id
    elif scene == "unknown":
        message_type = MessageType.FRIEND_MESSAGE
        raw_group_id = None
    else:
        raise ValueError(f"未知测试场景：{scene}")

    raw_message = SimpleNamespace(
        raw_data=raw_data,
        author=SimpleNamespace(**raw_author_fields),
        member=SimpleNamespace(nick=member_nick),
        group_openid=group_openid if scene == "group" else None,
        guild_id=guild_id if scene in {"channel", "direct"} else None,
        channel_id=channel_id if scene in {"channel", "direct"} else None,
        timestamp=timestamp,
    )
    message_obj = SimpleNamespace(
        raw_message=raw_message,
        sender=SimpleNamespace(user_id=wrapper_user_id, nickname="包装层名称"),
        group_id=raw_group_id if wrapper_group_id is None else wrapper_group_id,
    )
    return SimpleNamespace(
        message_obj=message_obj,
        get_platform_name=lambda: platform,
        get_platform_id=lambda: platform_id,
        get_message_type=lambda: message_type,
        get_sender_id=lambda: wrapper_user_id,
        get_group_id=lambda: message_obj.group_id,
    )


@pytest.mark.parametrize("platform", ["qq_official", "qq_official_webhook"])
def test_qq_official_group_uses_member_openid_for_both_transports(
    platform: str,
) -> None:
    """WebSocket 与 Webhook 群事件应共享场景 OpenID 身份语义。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(platform=platform)
    )

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.protocol == "qq_official"
    assert identity.identity_namespace == _NAMESPACE
    assert identity.stable_user_id == _OPENID
    assert identity.canonical_user_id == _CANONICAL_ID
    assert identity.conversation_sender_id == _CANONICAL_ID
    assert identity.identity_label == _LABEL
    assert identity.scope_type == "group"
    assert identity.scope_id == "GROUP-OPENID-1"
    assert identity.global_name == "官方昵称"
    assert identity.scope_name is None
    assert identity.display_name == "官方昵称"
    assert identity.name_field_states == {
        "nickname": NameFieldState.VALID,
        "card": NameFieldState.MISSING,
    }


def test_qq_official_c2c_reads_username_from_raw_data() -> None:
    """C2C SDK 对象丢弃名称时仍应读取 patched raw_data 的 username。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(scene="c2c", username="C2C 当前昵称")
    )

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.canonical_user_id == _CANONICAL_ID
    assert identity.scope_type == "private"
    assert identity.scope_id == _CANONICAL_ID
    assert identity.global_name == "C2C 当前昵称"
    assert identity.scope_name is None
    assert identity.observed_at == pytest.approx(1_784_781_296.0)


def test_qq_official_channel_uses_author_id_and_channel_scope() -> None:
    """频道消息应以 author.id 为身份并以文字子频道作为群作用域。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(scene="channel")
    )

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.stable_user_id == _OPENID
    assert identity.scope_type == "group"
    assert identity.scope_id == "CHANNEL-1"
    assert identity.global_name == "官方昵称"
    assert identity.scope_name == "频道昵称"
    assert identity.display_name == "频道昵称"


def test_qq_official_guild_direct_message_is_not_c2c() -> None:
    """频道私信应使用 author.id，但保持独立的 private 会话语义。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(scene="direct")
    )

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.stable_user_id == _OPENID
    assert identity.scope_type == "private"
    assert identity.scope_id == _CANONICAL_ID


def test_qq_official_union_openid_never_changes_canonical_identity() -> None:
    """可选 union_openid 不得触发无迁移的身份主键切换。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(scene="c2c", union_openid="UNION-DIFFERENT")
    )

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.stable_user_id == _OPENID
    assert identity.canonical_user_id == _CANONICAL_ID
    assert "UNION" not in identity.canonical_user_id


def test_qq_official_platform_instances_isolate_the_same_openid() -> None:
    """不同机器人实例收到相同 OpenID 文本时不得合并 canonical 身份。"""

    first = ProtocolIdentityResolver.default().resolve(_qq_official_event())
    second = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(platform_id="official-bot-2")
    )

    assert first.trust_status is IdentityTrust.TRUSTED
    assert second.trust_status is IdentityTrust.TRUSTED
    assert first.stable_user_id == second.stable_user_id == _OPENID
    assert first.identity_namespace != second.identity_namespace
    assert first.canonical_user_id != second.canonical_user_id


def test_qq_official_empty_name_keeps_identity_and_uses_stable_label() -> None:
    """官方名称为空时只回退展示标签，不得改变或丢弃 OpenID。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(scene="c2c", username="")
    )

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.canonical_user_id == _CANONICAL_ID
    assert identity.global_name is None
    assert identity.name_field_states["nickname"] is NameFieldState.EMPTY
    assert identity.display_name == _LABEL


def test_qq_official_invalid_timestamp_uses_injected_clock() -> None:
    """非法 RFC3339 时间不得影响身份，名称顺序应回退本地时钟。"""

    resolver = ProtocolIdentityResolver(
        (QQOfficialIdentityAdapter(clock=lambda: 321.5),)
    )

    identity = resolver.resolve(_qq_official_event(timestamp="not-rfc3339"))

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.observed_at == 321.5


@pytest.mark.parametrize(
    ("scene", "author_id", "wrapper_user_id"),
    [
        ("group", _OTHER_OPENID, _OPENID),
        ("c2c", _OPENID, _OTHER_OPENID),
        ("channel", _OPENID, _OTHER_OPENID),
    ],
)
def test_qq_official_conflicting_sender_evidence_fails_closed(
    scene: str,
    author_id: str,
    wrapper_user_id: str,
) -> None:
    """场景字段、author.id 或包装层 sender 冲突时不得产生身份。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(
            scene=scene,
            author_id=author_id,
            wrapper_user_id=wrapper_user_id,
        )
    )

    assert identity.trust_status is IdentityTrust.CONFLICT
    assert identity.stable_user_id is None
    assert identity.canonical_user_id is None
    assert identity.identity_label is None


def test_qq_official_conflicting_group_scope_fails_closed() -> None:
    """官方群作用域与 AstrBot wrapper group_id 不一致时应拒绝身份。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(wrapper_group_id="OTHER-GROUP")
    )

    assert identity.trust_status is IdentityTrust.CONFLICT
    assert identity.canonical_user_id is None


def test_qq_official_ambiguous_group_and_channel_shape_fails_closed() -> None:
    """同一载荷同时声称 QQ 群与频道场景时不得任选分支。"""

    event = _qq_official_event(scene="group")
    event.message_obj.raw_message.raw_data.update(
        {"guild_id": "GUILD-1", "channel_id": "CHANNEL-1"}
    )

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.CONFLICT
    assert identity.canonical_user_id is None


@pytest.mark.parametrize(
    "invalid_openid",
    [None, "", " ", 123, b"openid", "openid\n", "\x00openid", "A" * 91],
)
def test_qq_official_rejects_invalid_primary_openid(invalid_openid: object) -> None:
    """非法或过长的场景 OpenID 不得被清理、截断或写入目录。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(
            scene="c2c",
            openid=invalid_openid,
            author_id=invalid_openid,
            wrapper_user_id=invalid_openid,
        )
    )

    assert identity.trust_status is IdentityTrust.INVALID
    assert identity.canonical_user_id is None


@pytest.mark.parametrize("platform_id", [None, "", "bad id", "bad:id", 123])
def test_qq_official_rejects_invalid_platform_instance(platform_id: object) -> None:
    """缺失或非法平台实例边界时不能跨机器人应用合并 OpenID。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(platform_id=platform_id)
    )

    assert identity.trust_status is IdentityTrust.INVALID
    assert identity.canonical_user_id is None


def test_qq_official_preserves_opaque_openid_and_separates_onebot() -> None:
    """官方 OpenID 保留前导字符，并且 canonical 不得与 OneBot QQ 碰撞。"""

    official = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(
            scene="c2c",
            openid="00010001",
            author_id="00010001",
            wrapper_user_id="00010001",
        )
    )

    assert official.trust_status is IdentityTrust.TRUSTED
    assert official.stable_user_id == "00010001"
    assert official.canonical_user_id == f"{_NAMESPACE}:00010001"
    assert official.canonical_user_id != "10001"


def test_unknown_qq_official_message_shape_is_invalid() -> None:
    """已知官方消息平台上的未知载荷不得回退到不稳定 wrapper 身份。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _qq_official_event(scene="unknown")
    )

    assert identity.trust_status is IdentityTrust.INVALID
    assert identity.canonical_user_id is None
