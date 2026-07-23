"""协议稳定身份的公开轻量接口。"""

from .models import (
    IdentityProtocolAdapter,
    IdentityTrust,
    NameFieldState,
    ResolvedIdentity,
)
from .onebot11 import OneBot11IdentityAdapter
from .resolver import ProtocolIdentityResolver

__all__ = [
    "IdentityProtocolAdapter",
    "IdentityTrust",
    "NameFieldState",
    "OneBot11IdentityAdapter",
    "ProtocolIdentityResolver",
    "ResolvedIdentity",
]
