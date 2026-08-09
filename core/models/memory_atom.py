"""MemoryAtom 领域模型的兼容导出。"""

if __package__:
    from ..features.memory.domain.memory_atom import (
        AtomStatus,
        AtomType,
        DecayType,
        MemoryAtom,
        PrivacyLevel,
        compute_decay_score,
        compute_ttl,
    )
else:
    from core.features.memory.domain.memory_atom import (
        AtomStatus,
        AtomType,
        DecayType,
        MemoryAtom,
        PrivacyLevel,
        compute_decay_score,
        compute_ttl,
    )

__all__ = [
    "AtomStatus",
    "AtomType",
    "DecayType",
    "MemoryAtom",
    "PrivacyLevel",
    "compute_decay_score",
    "compute_ttl",
]
