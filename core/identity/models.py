"""协议身份领域模型的兼容导出。

模型唯一实现位于 ``core.features.identity.domain``；本模块保留旧导入路径，
确保现有协议适配器和宿主扩展不会在迁移期间得到第二套类型对象。
"""

from ..features.identity.domain.models import (
    IdentityProtocolAdapter,
    IdentityTrust,
    NameFieldState,
    ResolvedIdentity,
)

__all__ = [
    "IdentityProtocolAdapter",
    "IdentityTrust",
    "NameFieldState",
    "ResolvedIdentity",
]
