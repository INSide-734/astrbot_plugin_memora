"""canonical scope 的唯一解析边界与安全降级结果。"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from ..domain.models import IdentityTrust, ResolvedIdentity

SCOPE_RESOLVER_REVISION = "canonical-scope-v1"
CANONICAL_SCOPE_RESOLVER_REVISION = SCOPE_RESOLVER_REVISION
_MAX_SCOPE_TEXT = 256
_PRIVACY_BY_CHAT_TYPE = {"private": "confidential", "group": "public"}


class ScopeResolutionStatus(str, Enum):
    """canonical scope 解析结果的闭集状态。"""

    RESOLVED = "resolved"
    UNAVAILABLE = "scope_unavailable"


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    """保存一次可信 scope 解析的不可变快照。"""

    scope_key: str = ""
    chat_type: str | None = None
    privacy_level: str | None = None
    resolver_revision: str = ""
    identity_namespace: str = ""
    stable_user_id: str = ""
    scope_id: str = ""
    status: ScopeResolutionStatus = ScopeResolutionStatus.UNAVAILABLE
    reason_code: str = "scope_unavailable"

    def __post_init__(self) -> None:
        """规范化快照字段，并确保不可用结果不携带部分身份数据。"""

        values: dict[str, str] = {}
        for name in (
            "scope_key",
            "resolver_revision",
            "identity_namespace",
            "stable_user_id",
            "scope_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError("scope_snapshot_invalid")
            normalized = value.strip()
            if len(normalized) > _MAX_SCOPE_TEXT or any(
                unicodedata.category(char).startswith("C") for char in normalized
            ):
                raise ValueError("scope_snapshot_invalid")
            values[name] = normalized
        chat_type = self.chat_type.strip() if isinstance(self.chat_type, str) else None
        privacy = (
            self.privacy_level.strip() if isinstance(self.privacy_level, str) else None
        )
        status = (
            self.status
            if isinstance(self.status, ScopeResolutionStatus)
            else ScopeResolutionStatus(str(self.status))
        )
        reason = self.reason_code if isinstance(self.reason_code, str) else ""
        reason = reason.strip()
        if status is ScopeResolutionStatus.RESOLVED:
            if (
                not values["scope_key"]
                or chat_type not in _PRIVACY_BY_CHAT_TYPE
                or privacy not in {"public", "shared", "confidential"}
                or not values["resolver_revision"]
                or not values["identity_namespace"]
                or not values["stable_user_id"]
                or not values["scope_id"]
                or reason != "scope_resolved"
            ):
                raise ValueError("scope_snapshot_invalid")
        else:
            status = ScopeResolutionStatus.UNAVAILABLE
            reason = "scope_unavailable"
            chat_type = None
            privacy = None
            values = {name: "" for name in values}
        object.__setattr__(self, "scope_key", values["scope_key"])
        object.__setattr__(self, "resolver_revision", values["resolver_revision"])
        object.__setattr__(self, "identity_namespace", values["identity_namespace"])
        object.__setattr__(self, "stable_user_id", values["stable_user_id"])
        object.__setattr__(self, "scope_id", values["scope_id"])
        object.__setattr__(self, "chat_type", chat_type)
        object.__setattr__(self, "privacy_level", privacy)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_code", reason)

    @property
    def available(self) -> bool:
        """返回是否存在完整且可信的 canonical scope。"""

        return self.status is ScopeResolutionStatus.RESOLVED

    @property
    def scope_available(self) -> bool:
        """返回与 ``available`` 相同的兼容属性。"""

        return self.available

    def safe_projection(self) -> dict[str, object]:
        """返回不含 scope、身份和内部 revision 的低敏投影。"""

        return {
            "chat_type": self.chat_type,
            "privacy_level": self.privacy_level,
            "available": self.available,
            "reason_code": self.reason_code,
        }

    def to_dict(self) -> dict[str, object]:
        """返回供内部 summary 快照持久化的固定字段。"""

        return {
            "scope_key": self.scope_key,
            "chat_type": self.chat_type,
            "privacy_level": self.privacy_level,
            "resolver_revision": self.resolver_revision,
            "scope_id": self.scope_id or None,
            "status": self.status.value,
            "reason_code": self.reason_code,
        }


class CanonicalScopeResolver:
    """从可信协议身份或已持久化快照解析唯一 canonical scope。"""

    revision = SCOPE_RESOLVER_REVISION

    def __init__(self, revision: str = SCOPE_RESOLVER_REVISION) -> None:
        """冻结 resolver revision，防止运行时悄然改变 scope 语义。"""

        if revision != SCOPE_RESOLVER_REVISION:
            raise ValueError("scope_resolver_revision_invalid")
        self.revision = revision

    def resolve(
        self,
        identity: ResolvedIdentity | None = None,
        *,
        session_id: object = None,
        chat_type: object = None,
        group_id: object = None,
        scope_id: object = None,
        scope_key: object = None,
        privacy_level: object = None,
        resolver_revision: object = None,
    ) -> ScopeResolution:
        """仅从可信 ``ResolvedIdentity`` 解析 scope，任何不确定性均拒绝。"""

        if session_id is not None and not self._valid_text(session_id):
            return self._unavailable()
        if not isinstance(identity, ResolvedIdentity):
            return self._unavailable()
        if identity.trust_status is not IdentityTrust.TRUSTED:
            return self._unavailable()
        namespace = self._normalized_identifier(identity.identity_namespace)
        stable_user = self._normalized_identifier(identity.stable_user_id)
        canonical_user = self._normalized_identifier(identity.canonical_user_id)
        identity_scope_type = self._normalized_chat_type(identity.scope_type)
        identity_scope_id = self._normalized_identifier(identity.scope_id)
        if not namespace or not stable_user or not canonical_user:
            return self._unavailable()
        if not identity_scope_type or not identity_scope_id:
            return self._unavailable()
        if identity_scope_type == "private" and identity_scope_id != canonical_user:
            return self._unavailable()
        if (
            chat_type is not None
            and self._normalized_chat_type(chat_type) != identity_scope_type
        ):
            return self._unavailable()
        if group_id is not None:
            normalized_group = self._normalized_identifier(group_id)
            if identity_scope_type != "group" or normalized_group != identity_scope_id:
                return self._unavailable()
        if scope_id is not None:
            normalized_scope = self._normalized_identifier(scope_id)
            if normalized_scope != identity_scope_id:
                return self._unavailable()
        expected_privacy = _PRIVACY_BY_CHAT_TYPE[identity_scope_type]
        if privacy_level is not None and privacy_level != expected_privacy:
            return self._unavailable()
        if resolver_revision is not None and resolver_revision != self.revision:
            return self._unavailable()
        subject = (
            canonical_user if identity_scope_type == "private" else identity_scope_id
        )
        resolved_key = f"{identity_scope_type}:{namespace}:{subject}"
        if (
            scope_key is not None
            and self._normalized_identifier(scope_key) != resolved_key
        ):
            return self._unavailable()
        return ScopeResolution(
            scope_key=resolved_key,
            chat_type=identity_scope_type,
            privacy_level=expected_privacy,
            resolver_revision=self.revision,
            identity_namespace=namespace,
            stable_user_id=stable_user,
            scope_id=identity_scope_id,
            status=ScopeResolutionStatus.RESOLVED,
            reason_code="scope_resolved",
        )

    def resolve_from_identity(
        self,
        identity: ResolvedIdentity | None,
        **kwargs: object,
    ) -> ScopeResolution:
        """提供显式命名的身份解析入口，仍复用唯一 ``resolve`` 实现。"""

        return self.resolve(identity, **kwargs)

    def resolve_persisted(
        self,
        snapshot: ScopeResolution | Mapping[str, object] | None = None,
        **fields: object,
    ) -> ScopeResolution:
        """校验可信 Store 提供的完整快照，不从 session/persona/group 回推 scope。"""

        if snapshot is not None:
            if isinstance(snapshot, ScopeResolution):
                values: dict[str, object] = snapshot.to_dict()
            elif isinstance(snapshot, Mapping):
                values = dict(snapshot)
            else:
                return self._unavailable()
            values.update(fields)
        else:
            values = dict(fields)
        scope_key = self._normalized_identifier(values.get("scope_key"))
        chat_type = self._normalized_chat_type(values.get("chat_type"))
        privacy = self._normalized_identifier(values.get("privacy_level"))
        revision = self._normalized_identifier(values.get("resolver_revision"))
        if not scope_key or not chat_type or not privacy or revision != self.revision:
            return self._unavailable()
        expected_privacy = _PRIVACY_BY_CHAT_TYPE[chat_type]
        if privacy != expected_privacy:
            return self._unavailable()
        # 持久化快照必须携带主体绑定证据（scope_id）；缺失即不可用，
        # 不允许占位值或从 session/persona/group 反推。
        # identity_namespace/stable_user_id 为诊断辅助字段；快照缺失时从
        # resolver 自身生成的 scope_key（chat_type:namespace:subject）解码，
        # 并与 scope_id 主体段互校，不引入第二套身份来源。
        scope_value = self._normalized_identifier(values.get("scope_id"))
        namespace = self._normalized_identifier(values.get("identity_namespace"))
        stable_user = self._normalized_identifier(values.get("stable_user_id"))
        if not scope_value:
            return self._unavailable()
        prefix, _, remainder = scope_key.partition(":")
        namespace_from_key, separator, subject = remainder.partition(":")
        if not separator or not subject:
            return self._unavailable()
        if not namespace:
            namespace = namespace_from_key
        if not stable_user:
            if prefix == "private":
                stable_user = subject
            else:
                # 群聊 scope 的主体是群实例；stable_user 属于个体身份，
                # 快照未提供时以主体段占位诊断，不参与任何身份写入。
                stable_user = subject
        return ScopeResolution(
            scope_key=scope_key,
            chat_type=chat_type,
            privacy_level=privacy,
            resolver_revision=revision,
            identity_namespace=namespace,
            stable_user_id=stable_user,
            scope_id=scope_value,
            status=ScopeResolutionStatus.RESOLVED,
            reason_code="scope_resolved",
        )

    def resolve_snapshot(
        self,
        snapshot: ScopeResolution | Mapping[str, object] | None = None,
        **fields: object,
    ) -> ScopeResolution:
        """使用已持久化 resolver 快照的兼容命名入口。"""

        return self.resolve_persisted(snapshot, **fields)

    @staticmethod
    def _valid_text(value: object) -> bool:
        return bool(CanonicalScopeResolver._normalized_identifier(value))

    @staticmethod
    def _normalized_identifier(value: object) -> str:
        if not isinstance(value, str):
            return ""
        normalized = value.strip()
        if not normalized or len(normalized) > _MAX_SCOPE_TEXT:
            return ""
        if any(
            unicodedata.category(char).startswith("C") or char.isspace()
            for char in normalized
        ):
            return ""
        return normalized

    @staticmethod
    def _normalized_chat_type(value: object) -> str:
        normalized = CanonicalScopeResolver._normalized_identifier(value)
        return normalized if normalized in _PRIVACY_BY_CHAT_TYPE else ""

    @staticmethod
    def _unavailable() -> ScopeResolution:
        return ScopeResolution()


def resolve_canonical_scope(
    identity: ResolvedIdentity | None = None,
    **kwargs: object,
) -> ScopeResolution:
    """调用固定 resolver 解析 scope，普通失败统一返回 ``scope_unavailable``。"""

    return CanonicalScopeResolver().resolve(identity, **kwargs)


__all__ = [
    "CANONICAL_SCOPE_RESOLVER_REVISION",
    "CanonicalScopeResolver",
    "SCOPE_RESOLVER_REVISION",
    "ScopeResolution",
    "ScopeResolutionStatus",
    "resolve_canonical_scope",
]
