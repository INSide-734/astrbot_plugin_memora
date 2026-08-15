"""Memory Evolution Store 的 SQLite 写入协调边界。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from ...memory.application.write_coordinator import write_transaction
from ...memory.infrastructure.base_store import BaseStore

T = TypeVar("T")


class MemoryEvolutionWriteCoordinatorMixin(BaseStore):
    """为派生 Store 提供局部串行、全局重试与失败回滚。"""

    def __init__(self, db_path: str) -> None:
        """初始化基类连接配置与当前 Store 的局部写锁。"""

        super().__init__(db_path)
        self._write_lock = asyncio.Lock()

    async def _run_serialized_write(
        self,
        operation: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        """串行执行可重试写入，并在每个失败或取消尝试后回滚。

        参数:
            operation: 单次 SQLite 写入尝试；锁错误会由全局协调器重新调用。

        返回:
            写入操作的最终结果。

        异常:
            asyncio.CancelledError: 回滚后继续传播，不能把取消转为普通失败。

        副作用:
            每次失败尝试都会回滚持久连接，避免重试复用未提交事务或遗留写锁。
        """

        async def rollback_on_failure() -> T:
            """失败时清理当前持久连接的事务后重新抛出原始异常。"""

            try:
                return await operation()
            except BaseException:
                if self.connection is not None:
                    with contextlib.suppress(Exception):
                        await self.connection.rollback()
                raise

        async with self._write_lock:
            return await write_transaction(rollback_on_failure)
