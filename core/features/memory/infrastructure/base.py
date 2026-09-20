"""带共享 SQLite 工具与连接池能力的基础存储类。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ....shared.sql import apply_perf_pragmas

# ---------------------------------------------------------------------------
# 共享 SQLite 性能 PRAGMA 的单一事实来源已下沉到 ``core.shared.sql``；
# 本模块保留 ``apply_perf_pragmas`` 的 re-export 以兼容历史调用方。
# ---------------------------------------------------------------------------


class ConnectionPool:
    """支持可配置大小的 aiosqlite 连接池。

    管理固定大小的持久化 SQLite 连接池，避免每次操作都重复连接/关闭。
    在未初始化时会平滑回退，调用支持连接池的 `_connect()`` 仍能获得可用连接。
    """

    def __init__(self, db_path: str, pool_size: int = 3) -> None:
        self._db_path = db_path
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(
            maxsize=pool_size
        )
        self._size = pool_size
        # 追踪全部连接（含借出中的），保证 close() 不会漏掉任何句柄。
        self._connections: set[aiosqlite.Connection] = set()
        self._closed = False
        self._close_event = asyncio.Event()

    # ---- 生命周期 --------------------------------------------------

    async def initialize(self) -> None:
        """打开 `pool_size` 个持久连接并预热队列。"""
        for _ in range(self._size):
            conn = await aiosqlite.connect(self._db_path)
            await apply_perf_pragmas(conn)
            self._connections.add(conn)
            await self._pool.put(conn)

    @asynccontextmanager
    async def acquire(self):
        """从连接池借出一个连接，回滚残留事务后归还。"""
        if self._closed:
            raise RuntimeError("连接池已关闭")
        try:
            conn = self._pool.get_nowait()
        except asyncio.QueueEmpty:
            conn = await self._wait_for_connection()
        try:
            yield conn
        finally:
            with suppress(Exception):
                if conn.in_transaction:
                    # 借用方未收束的事务若随连接回到池中，下一个借用者会读到未提交数据。
                    await conn.rollback()
            await self._release(conn)

    async def _wait_for_connection(self) -> aiosqlite.Connection:
        """等待可用连接；池关闭时唤醒并抛错，而不是永久挂起。"""

        getter = asyncio.ensure_future(self._pool.get())
        closer = asyncio.ensure_future(self._close_event.wait())
        try:
            await asyncio.wait((getter, closer), return_when=asyncio.FIRST_COMPLETED)
        finally:
            closer.cancel()
            if not getter.done():
                getter.cancel()
        if self._closed:
            with suppress(BaseException):
                await getter
            raise RuntimeError("连接池已关闭")
        return getter.result()

    async def _release(self, conn: aiosqlite.Connection) -> None:
        """归还连接；池已关闭时直接关闭，避免句柄泄漏。"""

        if self._closed:
            await self._close_connection(conn)
            return
        await self._pool.put(conn)

    async def _close_connection(self, conn: aiosqlite.Connection) -> None:
        """关闭单个连接并从追踪集合移除（重复调用安全）。"""

        self._connections.discard(conn)
        with suppress(Exception):
            await conn.close()

    async def close(self) -> None:
        """关闭池中全部连接（含已借出者），并唤醒等待者、拒绝后续借出。"""

        self._closed = True
        # 先唤醒已在等待的借用者，再收束连接：等待者随后按 _closed 抛错。
        self._close_event.set()
        while True:
            try:
                conn = self._pool.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._close_connection(conn)
        for conn in list(self._connections):
            await self._close_connection(conn)
        self._connections.clear()

    @property
    def size(self) -> int:
        return self._size

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def available(self) -> int:
        return self._pool.qsize()


class BaseStore:
    """SQLite 存储类的共享基类。

    子类在设置 `db_path` 后，可在启动时调用一次 `init_pool()`。
    之后 `_connect()` 会透明地使用共享连接池；若连接池尚未初始化，
    则回退到一次性连接模式，以保持向后兼容。
    """

    _pool: ConnectionPool | None = None

    @classmethod
    async def init_pool(cls, db_path: str, pool_size: int = 3) -> None:
        """初始化共享连接池（启动时调用一次）。"""
        if cls._pool is not None:
            if cls._pool.db_path == db_path:
                return
            await cls._pool.close()
            cls._pool = None
        cls._pool = ConnectionPool(db_path, pool_size=pool_size)
        await cls._pool.initialize()

    @classmethod
    async def close_pool(cls) -> None:
        """关闭共享连接池（插件关闭时调用）。"""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None

    @asynccontextmanager
    async def _connect(self):
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                yield conn
            return

        # 回退：一次性连接（向后兼容）
        db = await aiosqlite.connect(self.db_path)
        try:
            await apply_perf_pragmas(db)
            yield db
        finally:
            await db.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_json(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        return json.dumps(payload if payload is not None else {}, ensure_ascii=False)

    @staticmethod
    def _from_json(payload: str | dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}


__all__ = ["BaseStore", "ConnectionPool", "apply_perf_pragmas"]
