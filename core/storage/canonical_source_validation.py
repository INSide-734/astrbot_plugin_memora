"""canonical source 校验实现的兼容导出。"""

from ..features.memory.infrastructure.canonical_source_validation import (
    CanonicalSourceState,
    load_canonical_source_states,
    source_matches_state,
    validate_domain_provenance,
)

__all__ = [
    "CanonicalSourceState",
    "load_canonical_source_states",
    "source_matches_state",
    "validate_domain_provenance",
]
