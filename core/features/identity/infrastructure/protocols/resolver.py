"""协议身份解析器的唯一选择与安全降级。"""

from __future__ import annotations

from collections.abc import Iterable

from ...domain.models import IdentityProtocolAdapter, IdentityTrust, ResolvedIdentity
from .registry import build_default_protocol_parsers


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
    """从不可变解析器集合中唯一选择并解析协议身份。"""

    def __init__(self, adapters: Iterable[IdentityProtocolAdapter]) -> None:
        """冻结适配器注册顺序，避免运行期动态接管事件。"""

        self._adapters = tuple(adapters)

    @classmethod
    def default(
        cls,
        additional_parsers: Iterable[IdentityProtocolAdapter] = (),
    ) -> "ProtocolIdentityResolver":
        """构建内置协议解析器，并按固定顺序追加调用方解析器。

        参数:
            additional_parsers: 追加到内置 manifest 后的协议解析器。

        返回:
            冻结解析器顺序的统一身份 resolver。
        """

        return cls(build_default_protocol_parsers(additional_parsers))

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
