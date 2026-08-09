"""稳定身份与会话协作 feature 的公开边界。"""

from .application.service import ProtocolIdentityService
from .contracts import IdentityDirectoryPort
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
