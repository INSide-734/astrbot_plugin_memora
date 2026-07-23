"""协议身份解析器的行为契约测试。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.identity import (
    IdentityProtocolAdapter,
    IdentityTrust,
    NameFieldState,
    OneBot11IdentityAdapter,
    ProtocolIdentityResolver,
    ResolvedIdentity,
)


def _onebot_event(
    *,
    message_type: str = "group",
    user_id: object = 10001,
    sender: object = None,
    group_id: object = 20001,
    sub_type: str = "normal",
    anonymous: object = None,
    wrapper_user_id: object = 10001,
    timestamp: object = 1_700_000_000,
    platform: str = "aiocqhttp",
) -> SimpleNamespace:
    """构造只包含解析器所需字段的 AstrBot 事件替身。"""

    if sender is None:
        sender = {
            "user_id": user_id,
            "nickname": "昵称甲",
            "card": "群名片甲",
        }
    raw_message = {
        "time": timestamp,
        "post_type": "message",
        "message_type": message_type,
        "sub_type": sub_type,
        "user_id": user_id,
        "sender": sender,
    }
    if message_type == "group":
        raw_message["group_id"] = group_id
        raw_message["anonymous"] = anonymous

    message_obj = SimpleNamespace(
        raw_message=raw_message,
        sender=SimpleNamespace(user_id=wrapper_user_id),
    )
    return SimpleNamespace(
        message_obj=message_obj,
        get_platform_name=lambda: platform,
    )


def _resolved_future_identity() -> ResolvedIdentity:
    """构造虚拟未来协议返回的可信身份。"""

    return ResolvedIdentity(
        protocol="future",
        identity_namespace="future-user",
        stable_user_id="member-7",
        canonical_user_id="future-user:member-7",
        scope_type="group",
        scope_id="room-3",
        global_name="未来用户",
        scope_name=None,
        display_name="未来用户",
        observed_at=123.0,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={"global_name": NameFieldState.VALID},
        conversation_sender_id="member-7",
        identity_label="Future:member-7",
    )


class _FutureAdapter:
    """验证通用解析器无需修改即可接入未来协议。"""

    def supports(self, event: object) -> bool:
        """仅接管标记为 future 的测试事件。"""

        return getattr(event, "protocol", None) == "future"

    def resolve(self, event: object) -> ResolvedIdentity:
        """返回固定的虚拟协议身份。"""

        return _resolved_future_identity()


class _ExplodingAdapter:
    """模拟适配器在解析阶段发生普通异常。"""

    def supports(self, event: object) -> bool:
        """声明接管全部测试事件。"""

        return True

    def resolve(self, event: object) -> ResolvedIdentity:
        """抛出普通异常以验证解析器安全降级。"""

        raise ValueError("测试异常不得外泄")


def test_onebot_group_uses_raw_qq_and_card() -> None:
    """群聊身份应使用原始 QQ，并优先展示群名片。"""

    identity = ProtocolIdentityResolver.default().resolve(_onebot_event())

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.protocol == "onebot11"
    assert identity.identity_namespace == "qq"
    assert identity.stable_user_id == "10001"
    assert identity.canonical_user_id == "10001"
    assert identity.scope_type == "group"
    assert identity.scope_id == "20001"
    assert identity.global_name == "昵称甲"
    assert identity.scope_name == "群名片甲"
    assert identity.display_name == "群名片甲"
    assert identity.observed_at == 1_700_000_000.0
    assert identity.conversation_sender_id == "10001"
    assert identity.identity_label == "QQ:10001"
    assert identity.name_field_states == {
        "nickname": NameFieldState.VALID,
        "card": NameFieldState.VALID,
    }


def test_onebot_private_uses_qq_nickname_without_group_card() -> None:
    """私聊身份应忽略 card，并以 QQ 昵称作为显示名称。"""

    event = _onebot_event(
        message_type="private",
        sender={"user_id": 10001, "nickname": "私聊昵称", "card": "不应使用"},
    )

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.scope_type == "private"
    assert identity.scope_id == "10001"
    assert identity.global_name == "私聊昵称"
    assert identity.scope_name is None
    assert identity.display_name == "私聊昵称"
    assert identity.name_field_states == {
        "nickname": NameFieldState.VALID,
        "card": NameFieldState.MISSING,
    }


def test_onebot_group_explicit_empty_card_is_preserved_as_state() -> None:
    """显式空群名片应标记为删除语义并回退到昵称。"""

    event = _onebot_event(
        sender={"user_id": 10001, "nickname": "昵称甲", "card": " \n\x00 "}
    )

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.scope_name is None
    assert identity.display_name == "昵称甲"
    assert identity.name_field_states["card"] is NameFieldState.EMPTY


def test_onebot_missing_sender_keeps_verified_qq_trusted() -> None:
    """sender 缺失时应保留顶层 QQ 的可信身份且不伪造名称。"""

    event = _onebot_event(sender={}, wrapper_user_id=None)

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.TRUSTED
    assert identity.canonical_user_id == "10001"
    assert identity.global_name is None
    assert identity.scope_name is None
    assert identity.display_name == "10001"
    assert identity.name_field_states == {
        "nickname": NameFieldState.MISSING,
        "card": NameFieldState.MISSING,
    }


@pytest.mark.parametrize("conflicting_id", [10002, "10002"])
def test_onebot_conflicting_raw_sender_id_fails_closed(conflicting_id: object) -> None:
    """顶层 QQ 与有效 raw sender QQ 冲突时不得继续解析。"""

    event = _onebot_event(sender={"user_id": conflicting_id, "nickname": "昵称甲"})

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.CONFLICT
    assert identity.stable_user_id is None
    assert identity.canonical_user_id is None


def test_onebot_conflicting_wrapper_sender_id_fails_closed() -> None:
    """AstrBot 包装层提供不同的有效 QQ 时也应拒绝身份。"""

    event = _onebot_event(wrapper_user_id=10002)

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.CONFLICT
    assert identity.canonical_user_id is None


@pytest.mark.parametrize(
    "invalid_user_id",
    [None, True, 0, -1, 9_223_372_036_854_775_808, 1.5, "", "12x"],
)
def test_onebot_rejects_invalid_top_level_qq(invalid_user_id: object) -> None:
    """顶层 QQ 不是正 int64 十进制整数时应标记为非法。"""

    event = _onebot_event(
        user_id=invalid_user_id,
        sender={},
        wrapper_user_id=None,
    )

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.trust_status is IdentityTrust.INVALID
    assert identity.canonical_user_id is None


def test_onebot_accepts_positive_int64_boundary_and_normalizes_digits() -> None:
    """QQ 上界应有效，十进制字符串应移除前导零。"""

    upper = 9_223_372_036_854_775_807
    upper_identity = ProtocolIdentityResolver.default().resolve(
        _onebot_event(user_id=upper, sender={}, wrapper_user_id=None)
    )
    padded_identity = ProtocolIdentityResolver.default().resolve(
        _onebot_event(user_id="00010001", sender={}, wrapper_user_id=None)
    )

    assert upper_identity.canonical_user_id == str(upper)
    assert upper_identity.trust_status is IdentityTrust.TRUSTED
    assert padded_identity.canonical_user_id == "10001"
    assert padded_identity.trust_status is IdentityTrust.TRUSTED


def test_onebot_normalizes_names_and_limits_to_128_codepoints() -> None:
    """名称应执行 NFKC、控制字符过滤、首尾清理和长度限制。"""

    nickname = " \x00ＡＢ\n" + ("名" * 140) + " "
    event = _onebot_event(
        sender={"user_id": 10001, "nickname": nickname, "card": None}
    )

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.global_name is not None
    assert identity.global_name.startswith("AB")
    assert "\x00" not in identity.global_name
    assert "\n" not in identity.global_name
    assert len(identity.global_name) == 128
    assert identity.scope_name is None
    assert identity.name_field_states["card"] is NameFieldState.INVALID


def test_onebot_marks_non_string_names_invalid() -> None:
    """非字符串昵称和群名片不得转换成可见名称。"""

    event = _onebot_event(
        sender={"user_id": 10001, "nickname": 123, "card": ["群名片"]}
    )

    identity = ProtocolIdentityResolver.default().resolve(event)

    assert identity.global_name is None
    assert identity.scope_name is None
    assert identity.display_name == "10001"
    assert identity.name_field_states == {
        "nickname": NameFieldState.INVALID,
        "card": NameFieldState.INVALID,
    }


def test_onebot_invalid_group_id_fails_closed() -> None:
    """群作用域无法验证时不得产生可信 QQ 身份。"""

    identity = ProtocolIdentityResolver.default().resolve(
        _onebot_event(group_id=0)
    )

    assert identity.trust_status is IdentityTrust.INVALID
    assert identity.canonical_user_id is None


def test_onebot_anonymous_group_uses_group_local_opaque_sender() -> None:
    """匿名群消息只生成群内 opaque sender，不创建 QQ 身份。"""

    anonymous = {"id": 77, "name": "匿名者", "flag": "opaque-flag"}
    first = ProtocolIdentityResolver.default().resolve(
        _onebot_event(sub_type="anonymous", anonymous=anonymous)
    )
    repeated = ProtocolIdentityResolver.default().resolve(
        _onebot_event(sub_type="anonymous", anonymous=anonymous)
    )
    other_group = ProtocolIdentityResolver.default().resolve(
        _onebot_event(group_id=20002, sub_type="anonymous", anonymous=anonymous)
    )

    assert first.trust_status is IdentityTrust.ANONYMOUS
    assert first.identity_namespace == ""
    assert first.stable_user_id is None
    assert first.canonical_user_id is None
    assert first.scope_type == "group"
    assert first.scope_id == "20001"
    assert first.conversation_sender_id is not None
    assert first.conversation_sender_id == repeated.conversation_sender_id
    assert first.conversation_sender_id != other_group.conversation_sender_id
    assert "20001" not in first.conversation_sender_id
    assert first.identity_label is None


def test_non_onebot_and_non_message_events_are_unsupported() -> None:
    """未注册平台和非 OneBot 消息事件应保留通用协议降级。"""

    other_platform = _onebot_event(platform="telegram")
    notice_event = _onebot_event()
    notice_event.message_obj.raw_message["post_type"] = "notice"

    assert (
        ProtocolIdentityResolver.default().resolve(other_platform).trust_status
        is IdentityTrust.UNSUPPORTED
    )
    assert (
        ProtocolIdentityResolver.default().resolve(notice_event).trust_status
        is IdentityTrust.UNSUPPORTED
    )


def test_duplicate_adapter_claims_fail_closed() -> None:
    """多个严格适配器同时接管同一事件时应拒绝选择。"""

    event = SimpleNamespace(protocol="future")
    resolver = ProtocolIdentityResolver((_FutureAdapter(), _FutureAdapter()))

    identity = resolver.resolve(event)

    assert identity.trust_status is IdentityTrust.CONFLICT
    assert identity.canonical_user_id is None


def test_future_adapter_satisfies_protocol_and_uses_shared_resolver() -> None:
    """未来协议只实现适配器契约即可复用统一解析器。"""

    adapter = _FutureAdapter()
    assert isinstance(adapter, IdentityProtocolAdapter)

    identity = ProtocolIdentityResolver((adapter,)).resolve(
        SimpleNamespace(protocol="future")
    )

    assert identity == _resolved_future_identity()
    with pytest.raises(TypeError):
        identity.name_field_states["global_name"] = NameFieldState.EMPTY
    assert replace(identity, display_name="新名称").canonical_user_id == identity.canonical_user_id


def test_adapter_exception_becomes_untrusted_identity() -> None:
    """普通适配器异常应安全降级且不得传播原始异常。"""

    identity = ProtocolIdentityResolver((_ExplodingAdapter(),)).resolve(object())

    assert identity.trust_status is IdentityTrust.INVALID
    assert identity.canonical_user_id is None
