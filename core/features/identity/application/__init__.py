"""身份 feature 的应用服务。"""

from .scope_resolver import (
    CANONICAL_SCOPE_RESOLVER_REVISION,
    SCOPE_RESOLVER_REVISION,
    CanonicalScopeResolver,
    ScopeResolution,
    ScopeResolutionStatus,
    resolve_canonical_scope,
)
from .service import ProtocolIdentityService

__all__ = [
    "CANONICAL_SCOPE_RESOLVER_REVISION",
    "CanonicalScopeResolver",
    "ProtocolIdentityService",
    "SCOPE_RESOLVER_REVISION",
    "ScopeResolution",
    "ScopeResolutionStatus",
    "resolve_canonical_scope",
]
