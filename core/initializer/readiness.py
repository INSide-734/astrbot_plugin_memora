"""插件初始化就绪状态的只读视图与等待逻辑。"""

import asyncio
import time
from typing import Any

from astrbot.api import logger


class InitializerReadinessMixin:
    """为插件初始化器提供状态查询和有界等待能力。"""

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
            "components_ready": {
                "db": self.db is not None,
                "graph_db": self.graph_db is not None,
                "memory_engine": self.memory_engine is not None,
                "memory_processor": self.memory_processor is not None,
                "memory_quarantine_store": self.memory_quarantine_store is not None,
                "memory_quality_gate": self.memory_quality_gate is not None,
                "conversation_manager": self.conversation_manager is not None,
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
