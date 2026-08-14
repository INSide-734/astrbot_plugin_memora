"""稳定身份 feature 的协议解析器边界。"""

from .onebot11 import OneBot11IdentityAdapter
from .qq_official import QQOfficialIdentityAdapter
from .registry import build_default_protocol_parsers
from .resolver import ProtocolIdentityResolver

__all__ = [
    "OneBot11IdentityAdapter",
    "ProtocolIdentityResolver",
    "QQOfficialIdentityAdapter",
    "build_default_protocol_parsers",
]
