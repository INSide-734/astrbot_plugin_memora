"""组合根的协议身份运行时构造。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import logger

from ...features.conversation.application.conversation_manager import (
    ConversationManager,
)
from ...features.identity.application.conversation_sync import (
    ConversationIdentitySynchronizer,
)
from ...features.identity.application.enricher import MemoryIdentityEnricher
from ...features.identity.application.runtime import ProtocolIdentityRuntime
from ...features.identity.application.service import ProtocolIdentityService
from ...features.identity.infrastructure.protocols import ProtocolIdentityResolver
from ...features.identity.infrastructure.store import ProtocolIdentityStore


async def build_identity_runtime(
    data_dir: str,
    conversation_manager: ConversationManager,
) -> ProtocolIdentityRuntime:
    """构造协议身份运行时；目录不可用时降级为仅解析模式。"""

    resolver = ProtocolIdentityResolver.default()
    store = ProtocolIdentityStore(str(Path(data_dir) / "memora.db"))

    async def close_store() -> None:
        """关闭身份 Store，避免初始化失败遗留连接。"""

        try:
            await store.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    try:
        await store.initialize()
    except asyncio.CancelledError:
        await close_store()
        raise
    except Exception:
        await close_store()
        logger.warning("协议身份目录初始化失败，已降级为仅解析模式")
        return ProtocolIdentityRuntime(resolver)

    try:
        service = ProtocolIdentityService(store)
        synchronizer = ConversationIdentitySynchronizer(
            conversation_manager.store,
            service,
            conversation_manager.invalidate_cache,
        )
        return ProtocolIdentityRuntime(
            resolver,
            service=service,
            synchronizer=synchronizer,
            store=store,
            enricher=MemoryIdentityEnricher(store),
        )
    except BaseException:
        await close_store()
        raise


__all__ = ["build_identity_runtime"]
