"""表达模式的 SQLite 持久化存储实现。"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from ..shared.list_sorting import SortQuery, order_by_clause
from ..storage.base import apply_perf_pragmas
from .models import ExpressionPattern, PatternScope

EXPRESSION_SORT_COLUMNS = {
    "situation": "situation COLLATE NOCASE",
    "expression": "expression COLLATE NOCASE",
    "weight": "weight",
    "usage_count": "usage_count",
    "created_at": "created_at",
    "last_used_at": "last_used_at",
}
_EXPRESSION_SQL_COLUMNS = {**EXPRESSION_SORT_COLUMNS, "id": "id"}


class ExpressionPatternStore:
    """基于 aiosqlite 持久化表达模式。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        """创建一次性 aiosqlite 连接，并应用共享 PRAGMA 配置。"""
        db = await aiosqlite.connect(self.db_path)
        try:
            await apply_perf_pragmas(db)
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        """创建 ``expression_patterns`` 表及其索引。"""
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS expression_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    situation TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    persona_id TEXT NOT NULL DEFAULT 'default',
                    user_id TEXT,
                    weight REAL NOT NULL DEFAULT 1.0,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    decayed_at REAL NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_expr_patterns_scope "
                "ON expression_patterns(group_id, persona_id, user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_expr_patterns_weight "
                "ON expression_patterns(group_id, persona_id, user_id, weight DESC)"
            )
            await db.commit()

    # ---- 行数据与模型互转辅助方法 ------------------------------------------------

    @staticmethod
    def _row_to_pattern(row: dict[str, Any]) -> ExpressionPattern:
        return ExpressionPattern(
            situation=row["situation"],
            expression=row["expression"],
            group_id=row["group_id"],
            persona_id=row["persona_id"],
            user_id=row["user_id"],
            weight=row["weight"],
            usage_count=row["usage_count"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            decayed_at=row["decayed_at"],
            pattern_id=row["id"],
        )

    @staticmethod
    def _row_to_dict(row: tuple | dict[str, Any]) -> dict[str, Any]:
        """将 aiosqlite 的行对象转换为普通字典。"""
        if isinstance(row, dict):
            return row
        return {
            "id": row[0],
            "situation": row[1],
            "expression": row[2],
            "group_id": row[3],
            "persona_id": row[4],
            "user_id": row[5],
            "weight": row[6],
            "usage_count": row[7],
            "created_at": row[8],
            "last_used_at": row[9],
            "decayed_at": row[10],
        }

    # ---- CRUD ----------------------------------------------------------------

    async def upsert(self, pattern: ExpressionPattern) -> ExpressionPattern:
        """插入新模式，或更新已存在的模式。"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                """
                SELECT id, weight FROM expression_patterns
                WHERE situation = ? AND expression = ? AND group_id = ?
                  AND persona_id = ? AND user_id IS ?
                ORDER BY weight DESC, id DESC
                LIMIT 1
                """,
                (
                    pattern.situation,
                    pattern.expression,
                    pattern.group_id,
                    pattern.persona_id,
                    pattern.user_id,
                ),
            )
            row = await cursor.fetchone()

            if row:
                new_weight = row["weight"] + 1.0
                await db.execute(
                    """
                    UPDATE expression_patterns
                    SET weight = ?, last_used_at = ?, decayed_at = ?
                    WHERE id = ?
                    """,
                    (new_weight, time.time(), time.time(), row["id"]),
                )
                await db.commit()
                pattern.weight = new_weight
                pattern.pattern_id = row["id"]
                return pattern

            # 插入新记录
            cursor = await db.execute(
                """
                INSERT INTO expression_patterns
                    (situation, expression, group_id, persona_id, user_id,
                     weight, usage_count, created_at, last_used_at, decayed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern.situation,
                    pattern.expression,
                    pattern.group_id,
                    pattern.persona_id,
                    pattern.user_id,
                    pattern.weight,
                    pattern.usage_count,
                    pattern.created_at,
                    pattern.last_used_at,
                    pattern.decayed_at,
                ),
            )
            pattern.pattern_id = cursor.lastrowid or 0
            await db.commit()
            return pattern

    async def get_by_scope(
        self,
        scope: PatternScope,
        limit: int = 100,
    ) -> list[ExpressionPattern]:
        """获取指定作用域下的模式，并按权重降序返回。"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM expression_patterns
                WHERE group_id = ? AND persona_id = ? AND user_id IS ?
                ORDER BY weight DESC
                LIMIT ?
                """,
                (scope.group_id, scope.persona_id, scope.user_id, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_pattern(dict(r)) for r in rows]

    async def count_by_scope(self, scope: PatternScope) -> int:
        """统计指定作用域下的模式数量。"""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM expression_patterns
                WHERE group_id = ? AND persona_id = ? AND user_id IS ?
                """,
                (scope.group_id, scope.persona_id, scope.user_id),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_top_by_weight(
        self,
        scope: PatternScope,
        limit: int = 10,
        sort: SortQuery = SortQuery("weight", "desc"),
    ) -> list[ExpressionPattern]:
        """按指定字段排序并返回作用域内前 N 条模式。"""
        _ = order_by_clause(
            sort,
            columns=_EXPRESSION_SQL_COLUMNS,
            tie_breaker="id",
        )
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM expression_patterns
                WHERE group_id = :group_id
                  AND persona_id = :persona_id
                  AND user_id IS :user_id
                ORDER BY
                  CASE WHEN :sort_by = 'situation' AND :sort_order = 'asc'
                       THEN situation END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'situation' AND :sort_order = 'desc'
                       THEN situation END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'expression' AND :sort_order = 'asc'
                       THEN expression END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'expression' AND :sort_order = 'desc'
                       THEN expression END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'weight' AND :sort_order = 'asc'
                       THEN weight END ASC,
                  CASE WHEN :sort_by = 'weight' AND :sort_order = 'desc'
                       THEN weight END DESC,
                  CASE WHEN :sort_by = 'usage_count' AND :sort_order = 'asc'
                       THEN usage_count END ASC,
                  CASE WHEN :sort_by = 'usage_count' AND :sort_order = 'desc'
                       THEN usage_count END DESC,
                  CASE WHEN :sort_by = 'created_at' AND :sort_order = 'asc'
                       THEN created_at END ASC,
                  CASE WHEN :sort_by = 'created_at' AND :sort_order = 'desc'
                       THEN created_at END DESC,
                  CASE WHEN :sort_by = 'last_used_at' AND :sort_order = 'asc'
                       THEN last_used_at END ASC,
                  CASE WHEN :sort_by = 'last_used_at' AND :sort_order = 'desc'
                       THEN last_used_at END DESC,
                  id ASC
                LIMIT :limit
                """,
                {
                    "group_id": scope.group_id,
                    "persona_id": scope.persona_id,
                    "user_id": scope.user_id,
                    "sort_by": sort.by,
                    "sort_order": sort.order,
                    "limit": limit,
                },
            )
            rows = await cursor.fetchall()
            return [self._row_to_pattern(dict(row)) for row in rows]

    async def delete_below_weight(
        self,
        scope: PatternScope,
        threshold: float,
    ) -> int:
        """删除权重小于等于阈值的模式，并返回删除行数。"""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                DELETE FROM expression_patterns
                WHERE group_id = ? AND persona_id = ? AND user_id IS ?
                  AND weight <= ?
                """,
                (scope.group_id, scope.persona_id, scope.user_id, threshold),
            )
            await db.commit()
            return cursor.rowcount

    async def delete_lowest_weight(
        self,
        scope: PatternScope,
        count: int,
    ) -> int:
        """删除指定作用域下权重最低的若干模式，并返回实际删除数。"""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                DELETE FROM expression_patterns
                WHERE id IN (
                    SELECT id FROM expression_patterns
                    WHERE group_id = ? AND persona_id = ? AND user_id IS ?
                    ORDER BY weight ASC
                    LIMIT ?
                )
                """,
                (scope.group_id, scope.persona_id, scope.user_id, count),
            )
            await db.commit()
            return cursor.rowcount

    async def update_weight(
        self,
        pattern_id: int,
        new_weight: float,
    ) -> None:
        """直接设置某条模式的权重。"""
        async with self._connect() as db:
            await db.execute(
                "UPDATE expression_patterns SET weight = ?, decayed_at = ? WHERE id = ?",
                (new_weight, time.time(), pattern_id),
            )
            await db.commit()

    async def mark_used(self, pattern_id: int) -> None:
        """增加 ``usage_count``，并更新 ``last_used_at``。"""
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE expression_patterns
                SET usage_count = usage_count + 1, last_used_at = ?
                WHERE id = ?
                """,
                (time.time(), pattern_id),
            )
            await db.commit()

    async def get_all_for_decay(self, scope: PatternScope) -> list[ExpressionPattern]:
        """获取指定作用域下的全部模式，供批量衰减使用。"""
        return await self.get_by_scope(scope, limit=999999)


__all__ = ["EXPRESSION_SORT_COLUMNS", "ExpressionPatternStore"]
