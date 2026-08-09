"""为独立 Store 提供持久 SQLite 连接基类。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from astrbot.api import logger

from .base import apply_perf_pragmas


class BaseStore:
    """为 SQLite 后端 Store 提供通用 CRUD 模式的基类。

    管理一个持久连接，子类覆盖 ``_create_tables()`` 定义 schema。
    所有读写操作都通过该连接执行；需要独立读取时可使用 ``_connect()``。
    """

    def __init__(self, db_path: str) -> None:
        """记录数据库路径，并等待显式初始化持久连接。"""
        self.db_path = db_path
        self.connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """打开持久连接，配置 row factory 并创建表。"""
        self.connection = await aiosqlite.connect(self.db_path)
        self.connection.row_factory = aiosqlite.Row
        await apply_perf_pragmas(self.connection)
        await self._create_tables()
        logger.info(f"[{self.__class__.__name__}] 数据库初始化完成: {self.db_path}")

    async def _create_tables(self) -> None:
        """由子类覆盖并创建自身表结构。"""

    async def close(self) -> None:
        """关闭持久连接并清除连接引用。"""
        if self.connection:
            await self.connection.close()
            self.connection = None
            logger.info(f"[{self.__class__.__name__}] 数据库连接已关闭")

    @asynccontextmanager
    async def _connect(self):
        """创建配置一致的临时连接，并在上下文退出时关闭。"""
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            await apply_perf_pragmas(db)
            yield db
        finally:
            await db.close()

    async def _execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """在持久连接上执行写查询。

        Args:
            sql: 固定 SQL 语句。
            params: 绑定参数。

        Returns:
            SQLite 游标。

        Raises:
            RuntimeError: Store 尚未初始化。
        """
        if not self.connection:
            raise RuntimeError(
                f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()"
            )
        return await self.connection.execute(sql, params)

    async def _execute_many(
        self, sql: str, params_list: list[tuple]
    ) -> aiosqlite.Cursor:
        """在持久连接上批量执行写查询。

        Args:
            sql: 固定 SQL 语句。
            params_list: 多组绑定参数。

        Returns:
            SQLite 游标。

        Raises:
            RuntimeError: Store 尚未初始化。
        """
        if not self.connection:
            raise RuntimeError(
                f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()"
            )
        return await self.connection.executemany(sql, params_list)

    async def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行读查询并返回全部字典行。

        Args:
            sql: 固定 SQL 语句。
            params: 绑定参数。

        Returns:
            查询结果字典列表。

        Raises:
            RuntimeError: Store 尚未初始化。
        """
        if not self.connection:
            raise RuntimeError(
                f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()"
            )
        cursor = await self.connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """执行读查询并返回首个字典行。

        Args:
            sql: 固定 SQL 语句。
            params: 绑定参数。

        Returns:
            首行字典；无结果时返回 ``None``。

        Raises:
            RuntimeError: Store 尚未初始化。
        """
        if not self.connection:
            raise RuntimeError(
                f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()"
            )
        cursor = await self.connection.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def _fetch_scalar(self, sql: str, params: tuple = ()) -> Any:
        """执行读查询并返回首列标量。

        Args:
            sql: 固定 SQL 语句。
            params: 绑定参数。

        Returns:
            首行首列；无结果时返回 ``None``。

        Raises:
            RuntimeError: Store 尚未初始化。
        """
        if not self.connection:
            raise RuntimeError(
                f"{self.__class__.__name__} 未初始化 -- 先调用 initialize()"
            )
        cursor = await self.connection.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row else None

    async def _commit(self) -> None:
        """提交持久连接上待处理的写操作。"""
        if self.connection:
            await self.connection.commit()

    async def _insert_many(
        self, table: str, columns: list[str], rows: list[tuple]
    ) -> int:
        """插入多行数据。

        Args:
            table: 内部固定表名。
            columns: 内部固定列名列表。
            rows: 待写入的值元组。

        Returns:
            插入行数；输入为空时返回 0。
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
        """按内部固定条件删除行。

        Args:
            table: 内部固定表名。
            **conditions: 内部固定列名到绑定值的映射。

        Returns:
            删除行数；无条件时返回 0。
        """
        if not conditions:
            return 0
        where_clause = " AND ".join(f"{key} = ?" for key in conditions)
        sql = f"DELETE FROM {table} WHERE {where_clause}"
        cursor = await self._execute(sql, tuple(conditions.values()))
        await self._commit()
        return cursor.rowcount

    async def _count_where(self, table: str, **conditions) -> int:
        """按内部固定条件统计行数。

        Args:
            table: 内部固定表名。
            **conditions: 内部固定列名到绑定值的映射。

        Returns:
            匹配行数。
        """
        if conditions:
            where_clause = " AND ".join(f"{key} = ?" for key in conditions)
            sql = f"SELECT COUNT(*) FROM {table} WHERE {where_clause}"
            return await self._fetch_scalar(sql, tuple(conditions.values())) or 0
        sql = f"SELECT COUNT(*) FROM {table}"
        return await self._fetch_scalar(sql) or 0


__all__ = ["BaseStore"]
