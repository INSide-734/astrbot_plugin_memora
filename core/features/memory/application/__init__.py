"""canonical memory feature 的应用协作边界。"""

from .atom_source_binding import (
    bind_atoms_to_canonical_source,
    validate_bound_atoms_match_canonical_source,
)

__all__ = [
    "bind_atoms_to_canonical_source",
    "validate_bound_atoms_match_canonical_source",
]
