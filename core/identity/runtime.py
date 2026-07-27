"""协议身份解析、名称观察和会话同步的运行时边界。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from ..storage.protocol_identity_store import ProtocolIdentityStore, StoredIdentity
from .conversation_sync import ConversationIdentitySynchronizer
from .memory import MemoryIdentityEnricher
from .models import IdentityTrust, ResolvedIdentity
from .resolver import ProtocolIdentityResolver
from .service import ProtocolIdentityService


class ProtocolIdentityRuntime:
    """始终解析协议身份，并按可用能力尽力维护名称目录。"""

    def __init__(
        self,
        resolver: ProtocolIdentityResolver | None = None,
        *,
        service: ProtocolIdentityService | None = None,
        synchronizer: ConversationIdentitySynchronizer | None = None,
        store: ProtocolIdentityStore | None = None,
        enricher: MemoryIdentityEnricher | None = None,
    ) -> None:
        """绑定固定解析器和可选持久化、只读增强组件。"""

        self._resolver = resolver or ProtocolIdentityResolver.default()
        self._service = service
        self._synchronizer = synchronizer
        self._store = store
        self._enricher = enricher

    @property
    def service(self) -> ProtocolIdentityService | None:
        """返回可选身份服务，解析器降级模式下为 ``None``。"""

        return self._service

    @property
    def synchronizer(self) -> ConversationIdentitySynchronizer | None:
        """返回可选会话名称同步器，解析器降级模式下为 ``None``。"""

        return self._synchronizer

    @property
    def enricher(self) -> MemoryIdentityEnricher | None:
        """返回可选历史别名只读增强器，目录降级模式下为 ``None``。"""

        return self._enricher

    async def get_identity(
        self,
        identity_namespace: str,
        stable_user_id: str,
    ) -> StoredIdentity | None:
        """读取稳定身份的当前目录记录；无 Store 时返回 ``None``。"""

        if self._store is None:
            return None
        return await self._store.get_identity(identity_namespace, stable_user_id)

    async def prepare(
        self,
        event: Any,
        *,
        writes_blocked: bool = False,
    ) -> ResolvedIdentity:
        """解析事件，并在可信且允许写入时尽力保存当前名称。"""

        identity = self._resolver.resolve(event)
        if (
            writes_blocked
            or identity.trust_status is not IdentityTrust.TRUSTED
        ):
            return identity

        try:
            if self._synchronizer is not None:
                session_id = getattr(event, "unified_msg_origin", "")
                await self._synchronizer.synchronize(
                    identity,
                    session_id=session_id if isinstance(session_id, str) else "",
                )
            elif self._service is not None:
                await self._service.observe(identity)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("协议身份名称同步失败，已保留本次解析结果")
        return identity

    async def close(self) -> None:
        """关闭运行时拥有的身份 Store；解析器降级模式无需处理。"""

        if self._store is None:
            return
        await self._store.close()
        self._store = None
