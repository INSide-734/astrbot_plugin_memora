"""OneBot 11 消息事件的严格身份解析器。"""

from __future__ import annotations

import hashlib
import time
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any

from ...domain.models import IdentityTrust, NameFieldState, ResolvedIdentity

_INT64_MAX = 9_223_372_036_854_775_807
_MISSING = object()


def _read_field(source: object, key: str) -> object:
    """从映射或对象读取字段，并区分缺失与显式空值。"""

    if isinstance(source, Mapping):
        return source[key] if key in source else _MISSING
    if source is None:
        return _MISSING
    return getattr(source, key, _MISSING)


def _normalize_positive_int64(value: object) -> str | None:
    """把正 int64 或 ASCII 十进制字符串规范化为无前导零文本。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value and value.isascii() and value.isdecimal():
        number = int(value)
    else:
        return None
    if number <= 0 or number > _INT64_MAX:
        return None
    return str(number)


def _normalize_name(value: object) -> tuple[str | None, NameFieldState]:
    """规范化协议名称，并返回其本次观察状态。"""

    if value is _MISSING:
        return None, NameFieldState.MISSING
    if not isinstance(value, str):
        return None, NameFieldState.INVALID

    normalized = unicodedata.normalize("NFKC", value)
    without_controls = "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith("C")
    )
    cleaned = without_controls.strip()
    if not cleaned:
        return None, NameFieldState.EMPTY
    return cleaned[:128], NameFieldState.VALID


def _resolve_observed_at(value: object, clock: Callable[[], float]) -> float:
    """优先采用合法 OneBot 时间，否则读取本地接收时间。"""

    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _INT64_MAX
    ):
        return float(value)
    return float(clock())


def _empty_identity(
    trust_status: IdentityTrust,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
    observed_at: float = 0.0,
) -> ResolvedIdentity:
    """构造不携带用户标识的 OneBot 解析结果。"""

    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id=None,
        canonical_user_id=None,
        scope_type=scope_type,
        scope_id=scope_id,
        global_name=None,
        scope_name=None,
        display_name=None,
        observed_at=observed_at,
        trust_status=trust_status,
        name_field_states={},
    )


def _anonymous_sender_id(group_id: str, raw_message: Mapping[str, Any]) -> str:
    """根据群作用域和匿名证据生成不可跨群关联的 opaque sender。"""

    anonymous = raw_message.get("anonymous")
    evidence = (
        group_id,
        _read_field(anonymous, "id"),
        _read_field(anonymous, "flag"),
        raw_message.get("user_id", _MISSING),
    )
    encoded = "\x1f".join(
        "<missing>" if value is _MISSING else str(value) for value in evidence
    ).encode("utf-8", errors="replace")
    return f"anonymous:{hashlib.sha256(encoded).hexdigest()[:32]}"


class OneBot11IdentityAdapter:
    """从严格识别的 OneBot 11 消息事件解析稳定 QQ 身份。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """初始化适配器，并允许测试注入本地时钟。"""

        self._clock = clock

    def supports(self, event: object) -> bool:
        """仅接管 aiocqhttp 提供的 OneBot 11 私聊或群聊消息。"""

        get_platform_name = getattr(event, "get_platform_name", None)
        if not callable(get_platform_name):
            return False
        try:
            platform_name = get_platform_name()
        except Exception:
            return False
        if platform_name != "aiocqhttp":
            return False

        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        return (
            isinstance(raw_message, Mapping)
            and raw_message.get("post_type") == "message"
            and raw_message.get("message_type") in {"private", "group"}
        )

    def resolve(self, event: object) -> ResolvedIdentity:
        """解析 OneBot QQ、作用域、名称状态、时间和匿名语义。"""

        if not self.supports(event):
            return _empty_identity(IdentityTrust.UNSUPPORTED)

        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message")
        message_type = raw_message["message_type"]
        observed_at = _resolve_observed_at(raw_message.get("time"), self._clock)

        if message_type == "group":
            group_id = _normalize_positive_int64(raw_message.get("group_id"))
            if group_id is None:
                return _empty_identity(
                    IdentityTrust.INVALID,
                    scope_type="group",
                    observed_at=observed_at,
                )
            if (
                raw_message.get("sub_type") == "anonymous"
                or raw_message.get("anonymous") is not None
            ):
                return self._resolve_anonymous(raw_message, group_id, observed_at)
            scope_type = "group"
            scope_id = group_id
        else:
            scope_type = "private"
            scope_id = None

        canonical_user_id = _normalize_positive_int64(raw_message.get("user_id"))
        if canonical_user_id is None:
            return _empty_identity(
                IdentityTrust.INVALID,
                scope_type=scope_type,
                scope_id=scope_id,
                observed_at=observed_at,
            )
        if scope_id is None:
            scope_id = canonical_user_id

        raw_sender = raw_message.get("sender")
        wrapper_sender = getattr(message_obj, "sender", None)
        for sender in (raw_sender, wrapper_sender):
            sender_id = _normalize_positive_int64(_read_field(sender, "user_id"))
            if sender_id is not None and sender_id != canonical_user_id:
                return _empty_identity(
                    IdentityTrust.CONFLICT,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    observed_at=observed_at,
                )

        global_name, nickname_state = _normalize_name(
            _read_field(raw_sender, "nickname")
        )
        if message_type == "group":
            scope_name, card_state = _normalize_name(_read_field(raw_sender, "card"))
        else:
            scope_name = None
            card_state = NameFieldState.MISSING
        display_name = scope_name or global_name or canonical_user_id

        return ResolvedIdentity(
            protocol="onebot11",
            identity_namespace="qq",
            stable_user_id=canonical_user_id,
            canonical_user_id=canonical_user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            global_name=global_name,
            scope_name=scope_name,
            display_name=display_name,
            observed_at=observed_at,
            trust_status=IdentityTrust.TRUSTED,
            name_field_states={
                "nickname": nickname_state,
                "card": card_state,
            },
            conversation_sender_id=canonical_user_id,
            identity_label=f"QQ:{canonical_user_id}",
        )

    def _resolve_anonymous(
        self,
        raw_message: Mapping[str, Any],
        group_id: str,
        observed_at: float,
    ) -> ResolvedIdentity:
        """解析匿名群消息，但不生成 QQ 或跨群稳定身份。"""

        anonymous_name, name_state = _normalize_name(
            _read_field(raw_message.get("anonymous"), "name")
        )
        return ResolvedIdentity(
            protocol="onebot11",
            identity_namespace="",
            stable_user_id=None,
            canonical_user_id=None,
            scope_type="group",
            scope_id=group_id,
            global_name=None,
            scope_name=anonymous_name,
            display_name=anonymous_name,
            observed_at=observed_at,
            trust_status=IdentityTrust.ANONYMOUS,
            name_field_states={"anonymous_name": name_state},
            conversation_sender_id=_anonymous_sender_id(group_id, raw_message),
            identity_label=None,
        )
