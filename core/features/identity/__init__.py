"""稳定身份与会话协作 feature 的公开边界。"""

from .application.service import ProtocolIdentityService
from .contracts import IDENTITY_SCHEMA_VERSION, IdentityDirectoryPort
from .domain.models import (
    IdentityMerger,
    IdentityObservation,
    IdentityProtocolAdapter,
    IdentityTrust,
    NameFieldState,
    ObservationMutation,
    ResolvedIdentity,
    StoredIdentity,
)
from .infrastructure.protocols import (
    OneBot11IdentityAdapter,
    ProtocolIdentityResolver,
    QQOfficialIdentityAdapter,
    build_default_protocol_parsers,
)
from .infrastructure.store import ProtocolIdentityStore

__all__ = [
    "IdentityMerger",
    "IdentityDirectoryPort",
    "IDENTITY_SCHEMA_VERSION",
    "IdentityObservation",
    "IdentityProtocolAdapter",
    "IdentityTrust",
    "NameFieldState",
    "ObservationMutation",
    "OneBot11IdentityAdapter",
    "ProtocolIdentityService",
    "ProtocolIdentityResolver",
    "ProtocolIdentityStore",
    "QQOfficialIdentityAdapter",
    "ResolvedIdentity",
    "StoredIdentity",
    "build_default_protocol_parsers",
]
