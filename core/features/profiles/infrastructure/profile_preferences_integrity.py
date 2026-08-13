"""ProfileStore 人工偏好写入的来源边界。"""

from __future__ import annotations

from ....shared.domain_provenance import DomainObjectOrigin
from ..domain.models import UserPreferences


def require_manual_preferences(preferences: UserPreferences | None) -> None:
    """拒绝把带 canonical 证据的偏好写入人工维护入口。"""

    if (
        preferences is not None
        and preferences.provenance is not None
        and preferences.provenance.origin is DomainObjectOrigin.DERIVED
    ):
        raise ValueError("derived_preferences_not_allowed")


__all__ = ["require_manual_preferences"]
