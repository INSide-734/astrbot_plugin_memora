"""插件初始化就绪状态的只读视图与等待逻辑。"""

import asyncio
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ...features.conversation.application.dedup_manager import DedupManager
from ...features.conversation.application.message_content_extractor import (
    MessageContentExtractor,
)
from ...features.identity.domain.models import IdentityTrust


class CaptureRuntime:
    """初始化器拥有的早期用户消息捕获能力。

    该能力只写共享 ConversationManager，不读取尚未完成发布的派生索引，
    也不启动总结、召回或演化。关闭时拒绝新事件并等待在途写入收束。
    """

    def __init__(
        self,
        conversation_manager: Any,
        identity_runtime: Any,
        config_manager: Any,
        write_guard_cb: Any,
    ) -> None:
        self.conversation_manager = conversation_manager
        self.identity_runtime = identity_runtime
        self.config_manager = config_manager
        self._write_guard_cb = write_guard_cb
        self._extractor = MessageContentExtractor()
        self._dedup = DedupManager(max_size=1000, ttl=300)
        self._state_lock = asyncio.Lock()
        self._dedup_lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()
        self._inflight = 0
        self._accepting = True

    async def capture_event(
        self, event: Any, *, content: str | None = None, req: Any = None
    ) -> bool:
        """捕获用户群聊或私聊事件并返回是否完成持久化。"""
        async with self._state_lock:
            if not self._accepting:
                return False
            self._inflight += 1
            self._drained.clear()
        try:
            message_type = event.get_message_type()
            is_group = message_type == MessageType.GROUP_MESSAGE
            private_values = (
                getattr(MessageType, "FRIEND_MESSAGE", None),
                getattr(MessageType, "PRIVATE_MESSAGE", None),
            )
            if is_group and not self.config_manager.get(
                "session_manager.enable_full_group_capture", True
            ):
                return False
            is_private = any(
                message_type == candidate
                for candidate in private_values
                if candidate is not None
            )
            if not is_private:
                is_private = getattr(message_type, "name", None) in {
                    "FRIEND_MESSAGE",
                    "PRIVATE_MESSAGE",
                } or getattr(message_type, "value", None) in {
                    "FriendMessage",
                    "PrivateMessage",
                    "FRIEND_MESSAGE",
                    "PRIVATE_MESSAGE",
                }
            if not is_group and not is_private:
                return False
            if event.get_sender_id() == event.get_self_id():
                return False
            session_id = getattr(event, "unified_msg_origin", "")
            if not isinstance(session_id, str) or not session_id:
                return False
            if self._writes_blocked():
                return False
            identity = await self.identity_runtime.prepare(event, writes_blocked=False)
            if identity.trust_status in {IdentityTrust.CONFLICT, IdentityTrust.INVALID}:
                return False
            if content is None:
                content = await self._extractor.extract_message_content(event, req)
            if not content:
                return False
            dedup_key = await self._dedup.build_dedup_key(
                event,
                session_id,
                content,
                sender_id_override=(
                    identity.canonical_user_id
                    if identity.trust_status is IdentityTrust.TRUSTED
                    else identity.conversation_sender_id
                    if identity.trust_status is IdentityTrust.ANONYMOUS
                    else None
                ),
            )
            async with self._dedup_lock:
                if await self._dedup.is_duplicate(dedup_key):
                    return False
                message = await self.conversation_manager.add_message_from_event(
                    event=event,
                    role="user",
                    content=content,
                    identity=identity,
                )
                if message is None:
                    return False
                if dedup_key:
                    await self._dedup.mark_processed(dedup_key)
                try:
                    setattr(event, "_memora_early_capture_persisted", True)
                except Exception:
                    pass
                return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("早期消息捕获失败", exc_info=True)
            return False
        finally:
            async with self._state_lock:
                self._inflight -= 1
                if self._inflight == 0:
                    self._drained.set()

    def _writes_blocked(self) -> bool:
        try:
            return bool(self._write_guard_cb())
        except Exception:
            return True

    async def close(self) -> None:
        """拒绝新捕获并等待所有在途捕获结束。"""
        async with self._state_lock:
            self._accepting = False
        await self._drained.wait()


class InitializerReadinessMixin:
    """为插件初始化器提供状态查询和有界等待能力。"""

    _initialization_complete: bool
    _initialization_failed: bool
    _initialization_error: str | None
    _provider_waiter: Any
    embedding_provider: Any | None
    llm_provider: Any | None
    db: Any | None
    graph_db: Any | None
    memory_engine: Any | None
    memory_processor: Any | None
    memory_quarantine_store: Any | None
    memory_quality_gate: Any | None
    conversation_manager: Any | None
    identity_runtime: Any | None
    capture_runtime: CaptureRuntime | None
    index_validator: Any | None
    memory_evolution_store: Any | None
    memory_evolution_manager: Any | None

    async def ensure_catalog_readiness(self, components: dict[str, Any]) -> None:
        """确认 catalog 已 ready 或已明确安全降级，再允许 Worker 启动。"""

        from ...features.observability.infrastructure.debug_reporter import (
            report_debug_event,
        )
        from ...shared.errors import InitializationError

        result = components.get("catalog_maintenance_result")
        if not isinstance(result, dict):
            result = {}
        decision = result.get("catalog_decision")
        if (
            decision not in {"ready", "degraded"}
            or result.get("safe_baseline") is not True
        ):
            report_debug_event(
                "plugin_initialized",
                component="initializer",
                stage="catalog_readiness",
                status="failed",
                reason_code="catalog_startup_unresolved",
                capability="topic_catalog",
            )
            raise InitializationError("topic_catalog_startup_unresolved")
        reason_code = result.get("reason_code")
        report_debug_event(
            "plugin_initialized",
            component="initializer",
            stage="catalog_readiness",
            status="completed" if decision == "ready" else "degraded",
            reason_code=(
                reason_code
                if isinstance(reason_code, str)
                else "catalog_startup_decision_invalid"
            ),
            capability="topic_catalog",
        )

    @property
    def is_initialized(self) -> bool:
        """返回插件共享组件是否已完成初始化。"""

        return self._initialization_complete

    @property
    def is_failed(self) -> bool:
        """返回插件初始化是否已经进入失败终态。"""

        return self._initialization_failed

    @property
    def error_message(self) -> str | None:
        """返回初始化失败消息；尚无失败时返回 ``None``。"""

        return self._initialization_error

    @property
    def provider_check_attempts(self) -> int:
        """返回 Provider 等待器已经执行的检查次数。"""

        return self._provider_waiter.attempts

    def get_readiness_snapshot(self) -> dict[str, Any]:
        """构建 Provider 与核心组件的只读就绪快照。"""

        missing_provider = []
        if self.embedding_provider is None:
            missing_provider.append("embedding")
        if self.llm_provider is None:
            missing_provider.append("llm")
        return {
            "is_initialized": self._initialization_complete,
            "is_failed": self._initialization_failed,
            "error_message": self._initialization_error,
            "provider_attempts": self.provider_check_attempts,
            "missing_provider": missing_provider,
            "capture_ready": self.capture_runtime is not None,
            "recall_ready": self._initialization_complete,
            "components_ready": {
                "db": self.db is not None,
                "graph_db": self.graph_db is not None,
                "memory_engine": self.memory_engine is not None,
                "memory_processor": self.memory_processor is not None,
                "memory_quarantine_store": self.memory_quarantine_store is not None,
                "memory_quality_gate": self.memory_quality_gate is not None,
                "conversation_manager": self.conversation_manager is not None,
                "identity_runtime": self.identity_runtime is not None,
                "capture_runtime": self.capture_runtime is not None,
                "index_validator": self.index_validator is not None,
                "memory_evolution_store": self.memory_evolution_store is not None,
                "memory_evolution_manager": self.memory_evolution_manager is not None,
            },
        }

    async def ensure_initialized(self, timeout: float = 30.0) -> bool:
        """在给定秒数内等待初始化终态，并返回是否就绪。"""

        if self._initialization_complete:
            return True
        if self._initialization_failed:
            return False
        start_time = time.time()
        while not self._initialization_complete and not self._initialization_failed:
            if time.time() - start_time > timeout:
                logger.error(f"等待插件初始化超时（{timeout}秒）")
                return False
            await asyncio.sleep(0.2)
        return self._initialization_complete


__all__ = ["InitializerReadinessMixin"]
