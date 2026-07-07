"""社交关系的 SQLite 持久化层。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiosqlite

from astrbot.api import logger

from ..storage.base import BaseStore
from .models import SocialRelation


class RelationStore(BaseStore):
    """``SocialRelation`` 记录的 SQLite 存储实现。"""

    _TABLE = "social_relations"
    _ALLOWED_TABLES = frozenset({"social_relations"})

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @classmethod
    def _quote_identifier(
        cls,
        identifier: str,
        *,
        allowed: frozenset[str],
        label: str,
    ) -> str:
        """在白名单校验通过后，为内部 SQL 标识符加引号。"""
        if identifier not in allowed:
            raise ValueError(f"Unsupported {label}: {identifier!r}")
        return f'"{identifier}"'

    @property
    def _table_sql(self) -> str:
        return self._quote_identifier(
            self._TABLE,
            allowed=self._ALLOWED_TABLES,
            label="relation table",
        )

    # ---- 表结构 ---------------------------------------------------------

    async def initialize(self) -> None:
        """若表不存在，则创建 ``social_relations`` 表。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_sql} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user TEXT NOT NULL,
                    to_user TEXT NOT NULL,
                    relation_type TEXT NOT NULL DEFAULT 'stranger',
                    strength REAL NOT NULL DEFAULT 0.1,
                    frequency INTEGER NOT NULL DEFAULT 0,
                    last_interaction REAL NOT NULL DEFAULT 0.0,
                    group_id TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(from_user, to_user, relation_type, group_id)
                )
            """)
            await db.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_social_from
                ON {table_sql}(from_user, group_id)
            """)
            await db.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_social_to
                ON {table_sql}(to_user, group_id)
            """)
            await db.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_social_group
                ON {table_sql}(group_id)
            """)
            await db.commit()

    # ---- 辅助方法 --------------------------------------------------------

    # 列名顺序与 social_relations 表的 SELECT * 输出保持一致。
    _COLUMNS = (
        "id", "from_user", "to_user", "relation_type", "strength",
        "frequency", "last_interaction", "group_id", "tags_json",
    )

    @classmethod
    def _row_to_dict(cls, row: aiosqlite.Row) -> dict[str, Any]:
        return dict(zip(cls._COLUMNS, row))

    # ---- CRUD -----------------------------------------------------------

    async def upsert_relation(self, rel: SocialRelation) -> None:
        """插入或更新一条 ``SocialRelation``。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            await db.execute(
                f"""
                INSERT INTO {table_sql}
                    (from_user, to_user, relation_type, strength, frequency,
                     last_interaction, group_id, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(from_user, to_user, relation_type, group_id) DO UPDATE SET
                    strength = excluded.strength,
                    frequency = excluded.frequency,
                    last_interaction = excluded.last_interaction,
                    tags_json = excluded.tags_json
                """,
                (
                    rel.from_user,
                    rel.to_user,
                    rel.relation_type,
                    rel.strength,
                    rel.frequency,
                    rel.last_interaction,
                    rel.group_id,
                    json.dumps(rel.tags, ensure_ascii=False),
                ),
            )
            await db.commit()

    async def get_relation(
        self,
        from_user: str,
        to_user: str,
        relation_type: str,
        group_id: str,
    ) -> SocialRelation | None:
        """按主键获取单条关系记录。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"""
                SELECT *
                FROM {table_sql}
                WHERE from_user = ?
                  AND to_user = ?
                  AND relation_type = ?
                  AND group_id = ?
                """,
                (from_user, to_user, relation_type, group_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return SocialRelation.from_row(self._row_to_dict(row))

    async def get_group_relations(self, group_id: str) -> list[SocialRelation]:
        """返回指定群组内的全部关系。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"""
                SELECT *
                FROM {table_sql}
                WHERE group_id = ?
                ORDER BY strength DESC
                """,
                (group_id,),
            )
            rows = await cursor.fetchall()
            return [SocialRelation.from_row(self._row_to_dict(r)) for r in rows]

    async def get_user_network(self, user_id: str) -> list[SocialRelation]:
        """返回涉及指定用户的全部关系（作为 from 或 to）。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"""
                SELECT *
                FROM {table_sql}
                WHERE from_user = ? OR to_user = ?
                ORDER BY strength DESC
                """,
                (user_id, user_id),
            )
            rows = await cursor.fetchall()
            return [SocialRelation.from_row(self._row_to_dict(r)) for r in rows]

    async def get_user_relations_in_group(
        self, user_id: str, group_id: str
    ) -> list[SocialRelation]:
        """返回指定群组中涉及该用户的全部关系。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"""
                SELECT *
                FROM {table_sql}
                WHERE (from_user = ? OR to_user = ?)
                  AND group_id = ?
                ORDER BY strength DESC
                """,
                (user_id, user_id, group_id),
            )
            rows = await cursor.fetchall()
            return [SocialRelation.from_row(self._row_to_dict(r)) for r in rows]

    async def delete_relation(
        self,
        from_user: str,
        to_user: str,
        relation_type: str,
        group_id: str,
    ) -> bool:
        """删除单条关系；若成功删除行则返回 ``True``。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"""
                DELETE FROM {table_sql}
                WHERE from_user = ?
                  AND to_user = ?
                  AND relation_type = ?
                  AND group_id = ?
                """,
                (from_user, to_user, relation_type, group_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_user_relations(
        self, user_id: str, group_id: str
    ) -> int:
        """删除指定群组内涉及该用户的全部关系。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"""
                DELETE FROM {table_sql}
                WHERE (from_user = ? OR to_user = ?)
                  AND group_id = ?
                """,
                (user_id, user_id, group_id),
            )
            await db.commit()
            return cursor.rowcount

    async def list_all(self) -> list[SocialRelation]:
        """返回全部记录（便于调试或迁移）。"""
        async with self._connect() as db:
            table_sql = self._table_sql
            cursor = await db.execute(
                f"SELECT * FROM {table_sql} ORDER BY id"
            )
            rows = await cursor.fetchall()
            return [SocialRelation.from_row(self._row_to_dict(r)) for r in rows]

    async def count(self) -> int:
        """返回总行数。"""
        async with self._connect() as db:
            cursor = await db.execute(f"SELECT COUNT(*) FROM {self._table_sql}")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def list_group_ids(self) -> list[str]:
        """返回社交关系表中所有非空且去重的群组 ID。"""
        async with self._connect() as db:
            cursor = await db.execute(
                f"""
                SELECT DISTINCT group_id
                FROM {self._table_sql}
                WHERE group_id <> ''
                ORDER BY group_id
                """
            )
            rows = await cursor.fetchall()
            return [str(row[0]) for row in rows if row and row[0]]


__all__ = ["RelationStore"]
