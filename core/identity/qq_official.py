"""QQ 官方机器人消息事件的严格 OpenID 身份解析器。"""

from __future__ import annotations

import hashlib
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import datetime

from astrbot.api.platform import MessageType

from .models import IdentityTrust, NameFieldState, ResolvedIdentity

_SUPPORTED_PLATFORMS = frozenset({"qq_official", "qq_official_webhook"})
_PROTOCOL = "qq_official"
_NAMESPACE_PREFIX = "qq-official"
# 11 字符协议前缀、两个分隔符和 24 字符实例键后，为 128 字符 canonical 留足空间。
_MAX_OPENID_CHARS = 90
_MAX_PLATFORM_ID_CHARS = 128
_MAX_SCOPE_ID_CHARS = 128
_INSTANCE_KEY_HEX_CHARS = 24
_MISSING = object()


def _read_field(source: object, key: str) -> object:
    """从映射或对象读取字段，并区分字段缺失与显式空值。"""

    if isinstance(source, Mapping):
        return source[key] if key in source else _MISSING
    if source is None:
        return _MISSING
    return getattr(source, key, _MISSING)


def _call_event_method(event: object, name: str) -> object:
    """调用无参事件方法；缺失或普通异常统一视为字段缺失。"""

    method = getattr(event, name, None)
    if not callable(method):
        return _MISSING
    try:
        return method()
    except Exception:
        return _MISSING


def _normalize_opaque_identifier(
    value: object,
    *,
    max_chars: int,
    forbid_colon: bool = False,
) -> str | None:
    """验证 opaque 标识但不改写大小写、Unicode 或前导字符。"""

    if not isinstance(value, str) or not value or len(value) > max_chars:
        return None
    if not value.isascii() or any(character.isspace() for character in value):
        return None
    if any(unicodedata.category(character).startswith("C") for character in value):
        return None
    if forbid_colon and ":" in value:
        return None
    return value


def _normalize_name(value: object) -> tuple[str | None, NameFieldState]:
    """规范化官方名称，并保留缺失、空值和非法值的观察语义。"""

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
    """优先解析带时区 RFC3339 时间，失败时使用本地接收时钟。"""

    if isinstance(value, str) and value:
        candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                timestamp = parsed.timestamp()
                if timestamp >= 0:
                    return float(timestamp)
        except (OverflowError, ValueError):
            pass
    return float(clock())


def _platform_instance_key(platform_id: object) -> str | None:
    """验证平台实例 ID，并生成不暴露原配置值的稳定实例键。"""

    normalized = _normalize_opaque_identifier(
        platform_id,
        max_chars=_MAX_PLATFORM_ID_CHARS,
        forbid_colon=True,
    )
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()[:_INSTANCE_KEY_HEX_CHARS]


def _empty_identity(
    trust_status: IdentityTrust,
    *,
    namespace: str = "",
    scope_type: str | None = None,
    scope_id: str | None = None,
    observed_at: float = 0.0,
) -> ResolvedIdentity:
    """构造不携带 OpenID 的 QQ 官方安全降级结果。"""

    return ResolvedIdentity(
        protocol=_PROTOCOL,
        identity_namespace=namespace,
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


def _evidence_status(value: object, expected: str) -> IdentityTrust | None:
    """校验可选用户证据；缺失允许，非法或冲突分别返回状态。"""

    if value is _MISSING:
        return None
    normalized = _normalize_opaque_identifier(
        value,
        max_chars=_MAX_OPENID_CHARS,
        forbid_colon=True,
    )
    if normalized is None:
        return IdentityTrust.INVALID
    if normalized != expected:
        return IdentityTrust.CONFLICT
    return None


def _scope_evidence_status(value: object, expected: str) -> IdentityTrust | None:
    """校验可选群或频道作用域证据，避免跨会话同步名称。"""

    if value is _MISSING or value is None:
        return None
    normalized = _normalize_opaque_identifier(
        value,
        max_chars=_MAX_SCOPE_ID_CHARS,
    )
    if normalized is None:
        return IdentityTrust.INVALID
    if normalized != expected:
        return IdentityTrust.CONFLICT
    return None


class QQOfficialIdentityAdapter:
    """从 AstrBot QQ 官方 WebSocket/Webhook 消息解析稳定 OpenID 身份。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        """初始化适配器，并允许测试注入本地接收时钟。"""

        self._clock = clock

    def supports(self, event: object) -> bool:
        """仅接管两个 QQ 官方平台的群聊或私聊消息事件。"""

        platform_name = _call_event_method(event, "get_platform_name")
        if platform_name not in _SUPPORTED_PLATFORMS:
            return False
        message_type = _call_event_method(event, "get_message_type")
        return message_type in {
            MessageType.GROUP_MESSAGE,
            MessageType.FRIEND_MESSAGE,
        }

    def resolve(self, event: object) -> ResolvedIdentity:
        """解析平台实例、消息场景、OpenID、名称、作用域与协议时间。"""

        if not self.supports(event):
            return _empty_identity(IdentityTrust.UNSUPPORTED)

        instance_key = _platform_instance_key(
            _call_event_method(event, "get_platform_id")
        )
        if instance_key is None:
            return _empty_identity(IdentityTrust.INVALID)
        namespace = f"{_NAMESPACE_PREFIX}:{instance_key}"

        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        raw_data = getattr(raw_message, "raw_data", None)
        if not isinstance(raw_data, Mapping):
            return _empty_identity(IdentityTrust.INVALID, namespace=namespace)
        author = raw_data.get("author")
        if not isinstance(author, Mapping):
            return _empty_identity(IdentityTrust.INVALID, namespace=namespace)

        observed_at = _resolve_observed_at(raw_data.get("timestamp"), self._clock)
        message_type = _call_event_method(event, "get_message_type")
        scene = self._resolve_scene(raw_data, author, message_type)
        if isinstance(scene, IdentityTrust):
            return _empty_identity(
                scene,
                namespace=namespace,
                observed_at=observed_at,
            )
        scene_name, primary_value, scope_type, scope_value = scene

        stable_user_id = _normalize_opaque_identifier(
            primary_value,
            max_chars=_MAX_OPENID_CHARS,
            forbid_colon=True,
        )
        scope_id = _normalize_opaque_identifier(
            scope_value,
            max_chars=_MAX_SCOPE_ID_CHARS,
        )
        if stable_user_id is None or scope_id is None:
            return _empty_identity(
                IdentityTrust.INVALID,
                namespace=namespace,
                scope_type=scope_type,
                observed_at=observed_at,
            )

        evidence_status = self._validate_user_evidence(
            event,
            message_obj,
            raw_message,
            author,
            scene_name=scene_name,
            stable_user_id=stable_user_id,
        )
        if evidence_status is not None:
            return _empty_identity(
                evidence_status,
                namespace=namespace,
                scope_type=scope_type,
                scope_id=scope_id,
                observed_at=observed_at,
            )
        if scope_type == "group":
            scope_status = self._validate_scope_evidence(
                event,
                message_obj,
                scope_id,
            )
            if scope_status is not None:
                return _empty_identity(
                    scope_status,
                    namespace=namespace,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    observed_at=observed_at,
                )

        canonical_user_id = f"{namespace}:{stable_user_id}"
        identity_label = f"QQ官方:{instance_key}:{stable_user_id}"
        if scope_type == "private":
            scope_id = canonical_user_id

        global_name, nickname_state = _normalize_name(
            _read_field(author, "username")
        )
        if scene_name == "channel":
            member = raw_data.get("member")
            scope_name, card_state = _normalize_name(_read_field(member, "nick"))
        else:
            scope_name = None
            card_state = NameFieldState.MISSING
        display_name = scope_name or global_name or identity_label

        return ResolvedIdentity(
            protocol=_PROTOCOL,
            identity_namespace=namespace,
            stable_user_id=stable_user_id,
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
            identity_label=identity_label,
        )

    @staticmethod
    def _resolve_scene(
        raw_data: Mapping[str, object],
        author: Mapping[str, object],
        message_type: object,
    ) -> tuple[str, object, str, object] | IdentityTrust:
        """根据官方场景字段唯一识别 QQ 群、C2C、频道或频道私信。"""

        has_group = "group_openid" in raw_data or "member_openid" in author
        has_c2c = "user_openid" in author
        has_channel = any(
            key in raw_data
            for key in ("channel_id", "guild_id", "src_guild_id", "direct_message")
        )
        claimed_scenes = sum((has_group, has_c2c, has_channel))
        if claimed_scenes > 1:
            return IdentityTrust.CONFLICT
        if claimed_scenes == 0:
            return IdentityTrust.INVALID

        if has_group:
            if message_type is not MessageType.GROUP_MESSAGE:
                return IdentityTrust.CONFLICT
            return (
                "group",
                _read_field(author, "member_openid"),
                "group",
                _read_field(raw_data, "group_openid"),
            )
        if has_c2c:
            if message_type is not MessageType.FRIEND_MESSAGE:
                return IdentityTrust.CONFLICT
            return (
                "c2c",
                _read_field(author, "user_openid"),
                "private",
                _read_field(author, "user_openid"),
            )

        if "channel_id" not in raw_data or "guild_id" not in raw_data:
            return IdentityTrust.INVALID
        if message_type is MessageType.GROUP_MESSAGE:
            return (
                "channel",
                _read_field(author, "id"),
                "group",
                _read_field(raw_data, "channel_id"),
            )
        if message_type is MessageType.FRIEND_MESSAGE:
            return (
                "direct",
                _read_field(author, "id"),
                "private",
                _read_field(author, "id"),
            )
        return IdentityTrust.CONFLICT

    @staticmethod
    def _validate_user_evidence(
        event: object,
        message_obj: object,
        raw_message: object,
        author: Mapping[str, object],
        *,
        scene_name: str,
        stable_user_id: str,
    ) -> IdentityTrust | None:
        """交叉校验 raw 对象、author.id 与 AstrBot sender 的用户标识。"""

        raw_author = getattr(raw_message, "author", None)
        scene_field = {
            "group": "member_openid",
            "c2c": "user_openid",
            "channel": "id",
            "direct": "id",
        }[scene_name]
        evidence = [
            _read_field(raw_author, scene_field),
            _read_field(author, "id"),
            _read_field(getattr(message_obj, "sender", None), "user_id"),
            _call_event_method(event, "get_sender_id"),
        ]
        for value in evidence:
            status = _evidence_status(value, stable_user_id)
            if status is not None:
                return status
        return None

    @staticmethod
    def _validate_scope_evidence(
        event: object,
        message_obj: object,
        scope_id: str,
    ) -> IdentityTrust | None:
        """交叉校验 AstrBot 包装层群 ID，拒绝把名称同步到错误会话。"""

        evidence = [
            getattr(message_obj, "group_id", _MISSING),
            _call_event_method(event, "get_group_id"),
        ]
        for value in evidence:
            status = _scope_evidence_status(value, scope_id)
            if status is not None:
                return status
        return None
