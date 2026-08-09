"""身份 feature 共享的纯领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, runtime_checkable


class IdentityTrust(str, Enum):
    """描述协议身份解析结果的可信状态。"""

    TRUSTED = "trusted"
    ANONYMOUS = "anonymous"
    CONFLICT = "conflict"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


class NameFieldState(str, Enum):
    """描述协议名称字段在本次事件中的观察状态。"""

    MISSING = "missing"
    EMPTY = "empty"
    INVALID = "invalid"
    VALID = "valid"


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """保存一次事件解析得到的只读协议身份快照。"""

    protocol: str
    identity_namespace: str
    stable_user_id: str | None
    canonical_user_id: str | None
    scope_type: str | None
    scope_id: str | None
    global_name: str | None
    scope_name: str | None
    display_name: str | None
    observed_at: float
    trust_status: IdentityTrust
    name_field_states: Mapping[str, NameFieldState]
    conversation_sender_id: str | None = None
    identity_label: str | None = None

    def __post_init__(self) -> None:
        """复制并冻结名称状态映射，避免调用方修改身份快照。"""

        object.__setattr__(
            self,
            "name_field_states",
            MappingProxyType(dict(self.name_field_states)),
        )


@runtime_checkable
class IdentityProtocolAdapter(Protocol):
    """定义严格协议身份适配器必须提供的只读接口。"""

    def supports(self, event: object) -> bool:
        """判断适配器是否应接管给定事件。"""

        ...

    def resolve(self, event: object) -> ResolvedIdentity:
        """把已接管事件解析为不可变身份快照。"""

        ...


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
    "IdentityProtocolAdapter",
    "IdentityMerger",
    "IdentityObservation",
    "IdentityTrust",
    "NameFieldState",
    "ObservationMutation",
    "ResolvedIdentity",
    "StoredIdentity",
]
