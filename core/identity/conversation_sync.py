"""协议身份名称与历史会话显示名称的作用域同步。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..features.identity.application.service import ProtocolIdentityService
from ..features.identity.domain.models import IdentityTrust, ResolvedIdentity
from ..storage.conversation_store import ConversationStore


class ConversationIdentitySynchronizer:
    """按可信协议作用域同步 user 消息名称并失效受影响缓存。"""

    def __init__(
        self,
        conversation_store: ConversationStore,
        identity_service: ProtocolIdentityService,
        invalidate_cache: Callable[[str], Awaitable[None]],
    ) -> None:
        """绑定会话 Store、身份服务和缓存失效回调。"""

        self._conversation_store = conversation_store
        self._identity_service = identity_service
        self._invalidate_cache = invalidate_cache

    async def synchronize(
        self,
        identity: ResolvedIdentity,
        *,
        session_id: str,
    ) -> set[str]:
        """保存可信名称后同步已证明作用域，并返回受影响 session。"""

        if (
            identity.trust_status is not IdentityTrust.TRUSTED
            or not identity.identity_namespace
            or not identity.stable_user_id
            or not identity.canonical_user_id
            or not identity.scope_type
            or identity.scope_id is None
        ):
            return set()

        stored = await self._identity_service.observe(identity)
        if stored is None:
            return set()

        scope_kwargs = self._conversation_scope(identity, session_id)
        if scope_kwargs is None:
            return set()
        old_names = await self._conversation_store.find_user_sender_names(
            sender_id=identity.canonical_user_id,
            **scope_kwargs,
        )
        aliases = self._alias_rows(identity, old_names, stored.display_name)
        if aliases:
            await self._identity_service.record_aliases(
                identity.identity_namespace,
                identity.stable_user_id,
                aliases,
                created_at=identity.observed_at,
            )

        changed_sessions = await self._conversation_store.update_user_sender_name(
            sender_id=identity.canonical_user_id,
            sender_name=stored.display_name,
            **scope_kwargs,
        )
        for changed_session in sorted(changed_sessions):
            try:
                await self._invalidate_cache(changed_session)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
        return changed_sessions

    @staticmethod
    def _conversation_scope(
        identity: ResolvedIdentity,
        session_id: str,
    ) -> dict[str, str | None] | None:
        """把协议作用域转换为 MessageStore 的封闭过滤条件。"""

        if identity.scope_type == "group":
            return {"session_id": session_id, "private_platform": None}
        if identity.scope_type == "private" and identity.protocol == "onebot11":
            return {"session_id": None, "private_platform": "aiocqhttp"}
        if identity.scope_type == "private" and identity.protocol == "qq_official":
            return {"session_id": session_id, "private_platform": None}
        return None

    @staticmethod
    def _alias_rows(
        identity: ResolvedIdentity,
        old_names: set[str],
        display_name: str,
    ) -> tuple[tuple[str, str, str], ...]:
        """把不同旧显示名称转换为身份目录的精确作用域别名。"""

        scope_type = "group" if identity.scope_type == "group" else "global"
        scope_id = identity.scope_id if scope_type == "group" else ""
        excluded = {
            display_name,
            identity.canonical_user_id or "",
            identity.identity_label or "",
        }
        return tuple(
            (scope_type, scope_id or "", name)
            for name in sorted(old_names)
            if name and name not in excluded
        )
