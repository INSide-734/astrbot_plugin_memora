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
    "ProtocolIdentityService",
    "ProtocolIdentityStore",
    "ResolvedIdentity",
    "StoredIdentity",
]
