"""身份目录 Store 与名称服务共享的纯领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class IdentityObservation(Protocol):
    """身份目录持久化所需的已解析事件最小视图。"""

    identity_namespace: str | None
    stable_user_id: str | None
    canonical_user_id: str | None
    scope_type: str | None
    scope_id: str | None
    observed_at: float


@dataclass(frozen=True, slots=True)
class StoredIdentity:
    """保存身份目录当前值及其作用域名称。"""

    identity_namespace: str
    stable_user_id: str
    canonical_user_id: str
    global_name: str | None
    scope_type: str | None
    scope_id: str | None
    scope_name: str | None
    display_name: str
    first_seen_at: float
    last_seen_at: float
    global_name_updated_at: float | None
    scope_name_updated_at: float | None
    admin_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationMutation:
    """描述名称服务计算出的单次身份观察变更。"""

    global_name_changed: bool
    global_name: str | None
    global_name_updated_at: float | None
    scope_name_changed: bool
    scope_name: str | None
    scope_name_updated_at: float | None
    aliases: tuple[tuple[str, str, str], ...] = ()


IdentityMerger = Callable[[StoredIdentity | None], ObservationMutation]


__all__ = [
    "IdentityMerger",
    "IdentityObservation",
    "ObservationMutation",
    "StoredIdentity",
]
