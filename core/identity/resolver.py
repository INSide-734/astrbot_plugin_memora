"""协议身份适配器的固定注册与唯一选择。"""

from __future__ import annotations

from collections.abc import Iterable

from .models import IdentityProtocolAdapter, IdentityTrust, ResolvedIdentity
from .onebot11 import OneBot11IdentityAdapter
from .qq_official import QQOfficialIdentityAdapter


def _untrusted_identity(trust_status: IdentityTrust) -> ResolvedIdentity:
    """构造不携带协议用户数据的安全降级结果。"""

    return ResolvedIdentity(
        protocol="",
        identity_namespace="",
        stable_user_id=None,
        canonical_user_id=None,
        scope_type=None,
        scope_id=None,
        global_name=None,
        scope_name=None,
        display_name=None,
        observed_at=0.0,
        trust_status=trust_status,
        name_field_states={},
    )


class ProtocolIdentityResolver:
    """从固定适配器集合中唯一选择并解析协议身份。"""

    def __init__(self, adapters: Iterable[IdentityProtocolAdapter]) -> None:
        """冻结适配器注册顺序，避免运行期动态接管事件。"""

        self._adapters = tuple(adapters)

    @classmethod
    def default(cls) -> "ProtocolIdentityResolver":
        """构建固定注册 OneBot 11 与 QQ 官方协议的默认解析器。"""

        return cls((OneBot11IdentityAdapter(), QQOfficialIdentityAdapter()))

    def resolve(self, event: object) -> ResolvedIdentity:
        """解析事件；重复接管或普通适配器异常均按不可信结果降级。"""

        claimed: list[IdentityProtocolAdapter] = []
        for adapter in self._adapters:
            try:
                if adapter.supports(event):
                    claimed.append(adapter)
            except Exception:
                return _untrusted_identity(IdentityTrust.INVALID)

        if not claimed:
            return _untrusted_identity(IdentityTrust.UNSUPPORTED)
        if len(claimed) != 1:
            return _untrusted_identity(IdentityTrust.CONFLICT)

        try:
            identity = claimed[0].resolve(event)
        except Exception:
            return _untrusted_identity(IdentityTrust.INVALID)
        if not isinstance(identity, ResolvedIdentity):
            return _untrusted_identity(IdentityTrust.INVALID)
        return identity
