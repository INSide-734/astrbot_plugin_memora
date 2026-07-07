"""轻量级 BaseStore CRUD mixin -- 提供标准查询助手和批量操作。

与 ``core/storage/base.py`` 中的 ``BaseStore`` (连接池模式) 互补：
本类是 per-instance connection 模式，适合不想依赖全局连接池的独立 Store。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from astrbot.api import logger

from .base import apply_perf_pragmas


class BaseStore:
    """为 SQLite 后端 Store 提供通用 CRUD 模式的基类。

    管理一个持久连接，子类覆盖 ``_create_tables()`` 定义 schema。
    所有读/写操作都通过此连接执行。该类为 **opt-in**：新 Store 可选继承，
    不影响已有的连接池模式的 Store。

    用法::

        class MyStore(BaseStore):
            def __init__(self, db_path: str) -> None:
                self.db_path = db_path

            async def _create_tables(self) -> None:
                await self._execute("CREATE TABLE IF NOT EXISTS ...")
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.connection: aiosqlite.Connection | None = None

    # ---- 生命周期 -----------------------------------------------------------------

    async def initialize(self) -> None:
        """打开持久连接，配置 row_factory 并创建表。"""
        self.connection = await aiosqlite.connect(self.db_path)
        self.connection.row_factory = aiosqlite.Row
        await apply_perf_pragmas(self.connection)
        await self._create_tables()
        logger.info(f"[{self.__class__.__name__}] 数据库初始化完成: {self.db_path}")

    async def _create_tables(self) -> None:
        """在子类中覆盖以创建表结构。默认不做任何操作。"""

    async def close(self) -> None:
        """关闭持久连接。"""
        if self.connection:
            await self.connection.close()
            self.connection = None
            logger.info(f"[{self.__class__.__name__}] 数据库连接已关闭")

    @asynccontextmanager
    async def _connect(self):
        """临时连接上下文管理器（用于只读查询或独立操作）。

        返回一个带有 PRAGMA 配置和 row_factory 的 aiosqlite.Connection。
        该连接在此上下文退出时自动关闭。
        """
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            await apply_perf_pragmas(db)
            yield db
        finally:
            await db.close()

    # ---- 查询助手 -----------------------------------------------------------------

    async def _execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """在持久连接上执行写查询。

        Raises:
            RuntimeError: 若未调用 ``initialize()``。
        """
        if not self.connection:
            raise RuntimeError(f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()")
        return await self.connection.execute(sql, params)

    async def _execute_many(self, sql: str, params_list: list[tuple]) -> aiosqlite.Cursor:
        """批量执行写查询。

        Raises:
            RuntimeError: 若未调用 ``initialize()``。
        """
        if not self.connection:
            raise RuntimeError(f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()")
        return await self.connection.executemany(sql, params_list)

    async def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行读查询，将所有行作为 dict 列表返回。

        Raises:
            RuntimeError: 若未调用 ``initialize()``。
        """
        if not self.connection:
            raise RuntimeError(f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()")
        cursor = await self.connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """执行读查询，将第一行作为 dict 返回；无结果时返回 None。

        Raises:
            RuntimeError: 若未调用 ``initialize()``。
        """
        if not self.connection:
            raise RuntimeError(f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()")
        cursor = await self.connection.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def _fetch_scalar(self, sql: str, params: tuple = ()) -> Any:
        """执行读查询，返回单个标量值；无结果时返回 None。

        Raises:
            RuntimeError: 若未调用 ``initialize()``。
        """
        if not self.connection:
            raise RuntimeError(f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()")
        cursor = await self.connection.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row else None

    async def _commit(self) -> None:
        """提交待处理的写操作。"""
        if self.connection:
            await self.connection.commit()

    # ---- 批量操作 -----------------------------------------------------------------

    async def _insert_many(
        self, table: str, columns: list[str], rows: list[tuple]
    ) -> int:
        """插入多行数据，返回插入的行数。

        Args:
            table: 目标表名。
            columns: 列名列表。
            rows: 值元组列表，每个元组对应一行。

        Returns:
            插入的行数。若 ``rows`` 为空则返回 0。
        """
        if not rows:
            return 0
        placeholders = ", ".join(["?" for _ in columns])
        cols = ", ".join(columns)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cursor = await self._execute_many(sql, rows)
        await self._commit()
        return cursor.rowcount

    async def _delete_where(self, table: str, **conditions) -> int:
        """按条件删除行，返回删除的行数。

        Args:
            table: 目标表名。
            **conditions: 列名=值 条件对。所有条件用 AND 连接。

        Returns:
            删除的行数。若无条件则返回 0。
        """
        if not conditions:
            return 0
        where_clause = " AND ".join(f"{k} = ?" for k in conditions)
        sql = f"DELETE FROM {table} WHERE {where_clause}"
        cursor = await self._execute(sql, tuple(conditions.values()))
        await self._commit()
        return cursor.rowcount

    async def _count_where(self, table: str, **conditions) -> int:
        """按条件统计行数。

        Args:
            table: 目标表名。
            **conditions: 可选的列名=值 过滤条件对。

        Returns:
            匹配的行数。
        """
        if conditions:
            where_clause = " AND ".join(f"{k} = ?" for k in conditions)
            sql = f"SELECT COUNT(*) FROM {table} WHERE {where_clause}"
            return await self._fetch_scalar(sql, tuple(conditions.values())) or 0
        else:
            sql = f"SELECT COUNT(*) FROM {table}"
            return await self._fetch_scalar(sql) or 0


__all__ = ["BaseStore"]
