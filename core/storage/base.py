"""带共享 SQLite 工具与连接池能力的基础存储类。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# 共享 SQLite 性能 PRAGMA —— 单一事实来源
# ---------------------------------------------------------------------------

_PERF_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("foreign_keys", "ON"),
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "30000"),
    ("cache_size", "-65536"),  # 64 MB page cache
    ("temp_store", "MEMORY"),
    ("mmap_size", "268435456"),  # 256 MB memory-mapped I/O
)


async def apply_perf_pragmas(conn: aiosqlite.Connection) -> None:
    """将共享性能 PRAGMA 设置应用到 `conn`。

    此处安全使用 f-string：`key` 与 `value` 均来自硬编码的
    `_PERF_PRAGMAS` 常量，而非用户输入。
    """
    for key, value in _PERF_PRAGMAS:
        # 安全：key/value 来自上方可信的 _PERF_PRAGMAS 常量
        await conn.execute(f"PRAGMA {key} = {value}")


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

    # ---- 生命周期 --------------------------------------------------

    async def initialize(self) -> None:
        """打开 `pool_size` 个持久连接并预热队列。"""
        for _ in range(self._size):
            conn = await aiosqlite.connect(self._db_path)
            await apply_perf_pragmas(conn)
            await self._pool.put(conn)

    @asynccontextmanager
    async def acquire(self):
        """从连接池借出一个连接，并在退出时归还。"""
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)

    async def close(self) -> None:
        """关闭连接池中的所有连接。"""
        while True:
            try:
                conn = self._pool.get_nowait()
            except asyncio.QueueEmpty:
                break
            with suppress(Exception):
                await conn.close()

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
