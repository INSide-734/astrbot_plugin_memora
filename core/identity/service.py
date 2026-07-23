"""协议身份名称观察与历史别名业务规则。"""

from __future__ import annotations

from collections.abc import Iterable

from ..storage.protocol_identity_store import (
    ObservationMutation,
    ProtocolIdentityStore,
    StoredIdentity,
)
from .models import IdentityTrust, NameFieldState, ResolvedIdentity


class ProtocolIdentityService:
    """根据可信身份观察维护全局昵称、群名片和历史别名。"""

    def __init__(self, store: ProtocolIdentityStore) -> None:
        """绑定身份目录 Store。"""

        self._store = store

    async def observe(self, identity: ResolvedIdentity) -> StoredIdentity | None:
        """观察可信身份；不可信或匿名事件不写入任何用户目录。"""

        if (
            identity.trust_status is not IdentityTrust.TRUSTED
            or not identity.identity_namespace
            or not identity.stable_user_id
            or not identity.canonical_user_id
            or not identity.scope_type
            or identity.scope_id is None
        ):
            return None
        return await self._store.merge_observation(
            identity,
            lambda current: self._plan_mutation(identity, current),
        )

    async def record_aliases(
        self,
        identity_namespace: str,
        stable_user_id: str,
        aliases: Iterable[tuple[str, str, str]],
        created_at: float | None = None,
    ) -> int:
        """为后续会话同步路径提供参数化别名记录入口。"""

        return await self._store.record_aliases(
            identity_namespace,
            stable_user_id,
            aliases,
            created_at=created_at,
        )

    def _plan_mutation(
        self,
        identity: ResolvedIdentity,
        current: StoredIdentity | None,
    ) -> ObservationMutation:
        """按观察时间计算全局昵称、作用域名称和别名变更。"""

        global_changed, global_name, global_updated_at, global_aliases = (
            self._plan_global_name(identity, current)
        )
        scope_changed, scope_name, scope_updated_at, scope_aliases = (
            self._plan_scope_name(identity, current)
        )
        return ObservationMutation(
            global_name_changed=global_changed,
            global_name=global_name,
            global_name_updated_at=global_updated_at,
            scope_name_changed=scope_changed,
            scope_name=scope_name,
            scope_name_updated_at=scope_updated_at,
            aliases=tuple(global_aliases + scope_aliases),
        )

    def _plan_global_name(
        self,
        identity: ResolvedIdentity,
        current: StoredIdentity | None,
    ) -> tuple[bool, str | None, float | None, list[tuple[str, str, str]]]:
        """计算昵称更新，空、缺失和非法昵称均保持原值。"""

        state = identity.name_field_states.get("nickname", NameFieldState.MISSING)
        candidate = identity.global_name
        if state is not NameFieldState.VALID or not candidate:
            return False, current.global_name if current else None, (
                current.global_name_updated_at if current else None
            ), []
        if current is None or current.global_name is None:
            return True, candidate, identity.observed_at, []
        if current.global_name == candidate:
            if (
                current.global_name_updated_at is None
                or identity.observed_at > current.global_name_updated_at
            ):
                return True, candidate, identity.observed_at, []
            return False, current.global_name, current.global_name_updated_at, []
        if (
            current.global_name_updated_at is None
            or identity.observed_at >= current.global_name_updated_at
        ):
            aliases = [("global", "", current.global_name)]
            return True, candidate, identity.observed_at, aliases
        aliases = [("global", "", candidate)]
        return False, current.global_name, current.global_name_updated_at, aliases

    def _plan_scope_name(
        self,
        identity: ResolvedIdentity,
        current: StoredIdentity | None,
    ) -> tuple[bool, str | None, float | None, list[tuple[str, str, str]]]:
        """计算群名片更新，显式空值按观察时间执行删除。"""

        state = identity.name_field_states.get("card", NameFieldState.MISSING)
        current_name = current.scope_name if current else None
        current_updated_at = current.scope_name_updated_at if current else None
        if identity.scope_type != "group":
            return False, current_name, current_updated_at, []
        if state is NameFieldState.VALID and identity.scope_name:
            candidate = identity.scope_name
            if current_name is None:
                if current_updated_at is None or identity.observed_at >= current_updated_at:
                    return True, candidate, identity.observed_at, []
                aliases = [("group", identity.scope_id or "", candidate)]
                return False, current_name, current_updated_at, aliases
            if current_name == candidate:
                if current_updated_at is None or identity.observed_at > current_updated_at:
                    return True, candidate, identity.observed_at, []
                return False, current_name, current_updated_at, []
            if current_updated_at is None or identity.observed_at >= current_updated_at:
                aliases = [("group", identity.scope_id or "", current_name)]
                return True, candidate, identity.observed_at, aliases
            aliases = [("group", identity.scope_id or "", candidate)]
            return False, current_name, current_updated_at, aliases
        if state is NameFieldState.EMPTY:
            if current_updated_at is None or identity.observed_at >= current_updated_at:
                aliases = (
                    [("group", identity.scope_id or "", current_name)]
                    if current_name is not None
                    else []
                )
                return (
                    True,
                    None,
                    identity.observed_at,
                    aliases,
                )
        return False, current_name, current_updated_at, []
