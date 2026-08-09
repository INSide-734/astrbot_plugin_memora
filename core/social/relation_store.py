"""社交关系的 SQLite 持久化层。"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from ..base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    compute_entity_revision,
)
from ..base.list_sorting import SortQuery, order_by_clause
from ..features.memory.infrastructure.sql_contract import SOCIAL_RELATIONS_TABLE
from ..storage.base import BaseStore
from .models import SocialRelation

SOCIAL_SORT_COLUMNS = {
    "from_user": "from_user COLLATE NOCASE",
    "to_user": "to_user COLLATE NOCASE",
    "group_id": "group_id COLLATE NOCASE",
    "relation_type": "relation_type COLLATE NOCASE",
    "strength": "strength",
    "frequency": "frequency",
    "last_interaction": "last_interaction",
}
_SOCIAL_SQL_COLUMNS = {
    **SOCIAL_SORT_COLUMNS,
    "id": "id",
}


class RelationStore(BaseStore):
    """``SocialRelation`` 记录的 SQLite 存储实现。"""

    _TABLE = SOCIAL_RELATIONS_TABLE
    _ALLOWED_TABLES = frozenset({SOCIAL_RELATIONS_TABLE})

    def __init__(self, db_path: str) -> None:
        """保存社交关系数据库路径。"""

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
        """验证固定关系表契约；执行查询始终使用静态 SQL 文本。"""

        return self._quote_identifier(
            self._TABLE,
            allowed=self._ALLOWED_TABLES,
            label="relation table",
        )

    # ---- 表结构 ---------------------------------------------------------

    async def initialize(self) -> None:
        """若表不存在，则创建 ``social_relations`` 表。"""
        async with self._connect() as db:
            _ = self._table_sql
            await db.execute("""
                CREATE TABLE IF NOT EXISTS social_relations (
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
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_social_from
                ON social_relations(from_user, group_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_social_to
                ON social_relations(to_user, group_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_social_group
                ON social_relations(group_id)
            """)
            await db.commit()

    # ---- 辅助方法 --------------------------------------------------------

    # 列名顺序与 social_relations 表的 SELECT * 输出保持一致。
    _COLUMNS = (
        "id",
        "from_user",
        "to_user",
        "relation_type",
        "strength",
        "frequency",
        "last_interaction",
        "group_id",
        "tags_json",
    )

    @classmethod
    def _row_to_dict(cls, row: aiosqlite.Row) -> dict[str, Any]:
        """按固定列顺序把 SQLite 行转换为领域映射。"""

        return dict(zip(cls._COLUMNS, row))

    # ---- CRUD -----------------------------------------------------------

    async def get_or_create_relation(self, rel: SocialRelation) -> SocialRelation:
        """仅在缺失时插入自动关系，并返回数据库中的当前记录。"""
        identity = (
            rel.from_user,
            rel.to_user,
            rel.relation_type,
            rel.group_id,
        )
        async with self._connect() as db:
            _ = self._table_sql
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """
                    INSERT INTO social_relations
                        (from_user, to_user, relation_type, strength, frequency,
                         last_interaction, group_id, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(from_user, to_user, relation_type, group_id)
                    DO NOTHING
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
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM social_relations
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    identity,
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("自动关系创建后无法读取")
                current = SocialRelation.from_row(self._row_to_dict(row))
                await db.commit()
                return current
            except BaseException:
                await db.rollback()
                raise

    async def apply_automatic_delta_if_exists(
        self,
        identity: tuple[str, str, str, str],
        *,
        delta: float,
        difficulty: float,
    ) -> tuple[SocialRelation, SocialRelation] | None:
        """锁内重读关系并仅更新自动学习拥有的互动字段。"""
        async with self._connect() as db:
            _ = self._table_sql
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM social_relations
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    identity,
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return None

                current = SocialRelation.from_row(self._row_to_dict(row))
                actual_delta = delta * (1.0 - difficulty)
                updated = SocialRelation(
                    from_user=current.from_user,
                    to_user=current.to_user,
                    relation_type=current.relation_type,
                    strength=max(
                        0.0,
                        min(1.0, current.strength + actual_delta),
                    ),
                    frequency=current.frequency + 1,
                    last_interaction=time.time(),
                    group_id=current.group_id,
                    tags=list(current.tags),
                )
                await db.execute(
                    """
                    UPDATE social_relations
                    SET strength = ?, frequency = ?, last_interaction = ?
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    (
                        updated.strength,
                        updated.frequency,
                        updated.last_interaction,
                        *identity,
                    ),
                )
                await db.commit()
                return current, updated
            except BaseException:
                await db.rollback()
                raise

    async def create_relation_strict(self, rel: SocialRelation) -> SocialRelation:
        """严格插入关系；复合键已存在时不覆盖现有记录。"""
        async with self._connect() as db:
            _ = self._table_sql
            try:
                await db.execute(
                    """
                    INSERT INTO social_relations
                        (from_user, to_user, relation_type, strength, frequency,
                         last_interaction, group_id, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                raise EntityAlreadyExistsError("社交关系已存在") from exc
            except BaseException:
                await db.rollback()
                raise
        return rel

    async def update_relation_if_revision(
        self,
        identity: tuple[str, str, str, str],
        *,
        relation_type: str,
        strength: float,
        tags: list[str],
        expected_revision: str,
    ) -> SocialRelation:
        """在同一事务内检查修订版本并绝对更新关系业务字段。"""
        from_user, to_user, current_type, group_id = identity
        async with self._connect() as db:
            _ = self._table_sql
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM social_relations
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    identity,
                )
                row = await cursor.fetchone()
                if row is None:
                    raise EntityNotFoundError("社交关系不存在")

                current = SocialRelation.from_row(self._row_to_dict(row))
                current_serialized = current.to_dict()
                current_revision = compute_entity_revision(current_serialized)
                if current_revision != expected_revision:
                    raise EditConflictError(
                        current_serialized,
                        current_revision,
                    )

                if relation_type != current_type:
                    cursor = await db.execute(
                        """
                        SELECT 1
                        FROM social_relations
                        WHERE from_user = ?
                          AND to_user = ?
                          AND relation_type = ?
                          AND group_id = ?
                        """,
                        (from_user, to_user, relation_type, group_id),
                    )
                    if await cursor.fetchone() is not None:
                        raise EntityAlreadyExistsError("社交关系已存在")

                updated = SocialRelation(
                    from_user=from_user,
                    to_user=to_user,
                    relation_type=relation_type,
                    strength=strength,
                    frequency=current.frequency,
                    last_interaction=current.last_interaction,
                    group_id=group_id,
                    tags=list(tags),
                )
                await db.execute(
                    """
                    UPDATE social_relations
                    SET relation_type = ?, strength = ?, tags_json = ?
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    (
                        updated.relation_type,
                        updated.strength,
                        json.dumps(updated.tags, ensure_ascii=False),
                        from_user,
                        to_user,
                        current_type,
                        group_id,
                    ),
                )
                await db.commit()
                return updated
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                raise EntityAlreadyExistsError("社交关系已存在") from exc
            except BaseException:
                await db.rollback()
                raise

    async def delete_relation_if_revision(
        self,
        identity: tuple[str, str, str, str],
        *,
        expected_revision: str,
    ) -> bool:
        """在同一事务内检查修订版本并删除关系。"""
        async with self._connect() as db:
            _ = self._table_sql
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    """
                    SELECT *
                    FROM social_relations
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    identity,
                )
                row = await cursor.fetchone()
                if row is None:
                    raise EntityNotFoundError("社交关系不存在")

                current = SocialRelation.from_row(self._row_to_dict(row))
                current_serialized = current.to_dict()
                current_revision = compute_entity_revision(current_serialized)
                if current_revision != expected_revision:
                    raise EditConflictError(
                        current_serialized,
                        current_revision,
                    )

                await db.execute(
                    """
                    DELETE FROM social_relations
                    WHERE from_user = ?
                      AND to_user = ?
                      AND relation_type = ?
                      AND group_id = ?
                    """,
                    identity,
                )
                await db.commit()
                return True
            except BaseException:
                await db.rollback()
                raise

    async def upsert_relation(self, rel: SocialRelation) -> None:
        """插入或更新一条 ``SocialRelation``。"""
        async with self._connect() as db:
            _ = self._table_sql
            await db.execute(
                """
                INSERT INTO social_relations
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
            _ = self._table_sql
            cursor = await db.execute(
                """
                SELECT *
                FROM social_relations
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

    async def get_group_relations(
        self,
        group_id: str,
        sort: SortQuery = SortQuery("strength", "desc"),
    ) -> list[SocialRelation]:
        """返回指定群组内的全部关系。"""
        _ = order_by_clause(
            sort,
            columns=_SOCIAL_SQL_COLUMNS,
            tie_breaker="id",
        )
        async with self._connect() as db:
            _ = self._table_sql
            cursor = await db.execute(
                """
                SELECT *
                FROM social_relations
                WHERE group_id = :group_id
                ORDER BY
                  CASE WHEN :sort_by = 'from_user' AND :sort_order = 'asc'
                       THEN from_user END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'from_user' AND :sort_order = 'desc'
                       THEN from_user END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'to_user' AND :sort_order = 'asc'
                       THEN to_user END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'to_user' AND :sort_order = 'desc'
                       THEN to_user END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'group_id' AND :sort_order = 'asc'
                       THEN group_id END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'group_id' AND :sort_order = 'desc'
                       THEN group_id END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'relation_type' AND :sort_order = 'asc'
                       THEN relation_type END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'relation_type' AND :sort_order = 'desc'
                       THEN relation_type END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'strength' AND :sort_order = 'asc'
                       THEN strength END ASC,
                  CASE WHEN :sort_by = 'strength' AND :sort_order = 'desc'
                       THEN strength END DESC,
                  CASE WHEN :sort_by = 'frequency' AND :sort_order = 'asc'
                       THEN frequency END ASC,
                  CASE WHEN :sort_by = 'frequency' AND :sort_order = 'desc'
                       THEN frequency END DESC,
                  CASE WHEN :sort_by = 'last_interaction' AND :sort_order = 'asc'
                       THEN last_interaction END ASC,
                  CASE WHEN :sort_by = 'last_interaction' AND :sort_order = 'desc'
                       THEN last_interaction END DESC,
                  id ASC
                """,
                {
                    "group_id": group_id,
                    "sort_by": sort.by,
                    "sort_order": sort.order,
                },
            )
            rows = await cursor.fetchall()
            return [SocialRelation.from_row(self._row_to_dict(r)) for r in rows]

    async def get_user_network(self, user_id: str) -> list[SocialRelation]:
        """返回涉及指定用户的全部关系（作为 from 或 to）。"""
        async with self._connect() as db:
            _ = self._table_sql
            cursor = await db.execute(
                """
                SELECT *
                FROM social_relations
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
            _ = self._table_sql
            cursor = await db.execute(
                """
                SELECT *
                FROM social_relations
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
            _ = self._table_sql
            cursor = await db.execute(
                """
                DELETE FROM social_relations
                WHERE from_user = ?
                  AND to_user = ?
                  AND relation_type = ?
                  AND group_id = ?
                """,
                (from_user, to_user, relation_type, group_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_user_relations(self, user_id: str, group_id: str) -> int:
        """删除指定群组内涉及该用户的全部关系。"""
        async with self._connect() as db:
            _ = self._table_sql
            cursor = await db.execute(
                """
                DELETE FROM social_relations
                WHERE (from_user = ? OR to_user = ?)
                  AND group_id = ?
                """,
                (user_id, user_id, group_id),
            )
            await db.commit()
            return cursor.rowcount

    async def list_all(
        self,
        sort: SortQuery = SortQuery("last_interaction", "desc"),
    ) -> list[SocialRelation]:
        """返回全部记录（便于调试或迁移）。"""
        _ = order_by_clause(
            sort,
            columns=_SOCIAL_SQL_COLUMNS,
            tie_breaker="id",
        )
        async with self._connect() as db:
            _ = self._table_sql
            cursor = await db.execute(
                """
                SELECT * FROM social_relations
                ORDER BY
                  CASE WHEN :sort_by = 'from_user' AND :sort_order = 'asc'
                       THEN from_user END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'from_user' AND :sort_order = 'desc'
                       THEN from_user END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'to_user' AND :sort_order = 'asc'
                       THEN to_user END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'to_user' AND :sort_order = 'desc'
                       THEN to_user END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'group_id' AND :sort_order = 'asc'
                       THEN group_id END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'group_id' AND :sort_order = 'desc'
                       THEN group_id END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'relation_type' AND :sort_order = 'asc'
                       THEN relation_type END COLLATE NOCASE ASC,
                  CASE WHEN :sort_by = 'relation_type' AND :sort_order = 'desc'
                       THEN relation_type END COLLATE NOCASE DESC,
                  CASE WHEN :sort_by = 'strength' AND :sort_order = 'asc'
                       THEN strength END ASC,
                  CASE WHEN :sort_by = 'strength' AND :sort_order = 'desc'
                       THEN strength END DESC,
                  CASE WHEN :sort_by = 'frequency' AND :sort_order = 'asc'
                       THEN frequency END ASC,
                  CASE WHEN :sort_by = 'frequency' AND :sort_order = 'desc'
                       THEN frequency END DESC,
                  CASE WHEN :sort_by = 'last_interaction' AND :sort_order = 'asc'
                       THEN last_interaction END ASC,
                  CASE WHEN :sort_by = 'last_interaction' AND :sort_order = 'desc'
                       THEN last_interaction END DESC,
                  id ASC
                """,
                {"sort_by": sort.by, "sort_order": sort.order},
            )
            rows = await cursor.fetchall()
            return [SocialRelation.from_row(self._row_to_dict(r)) for r in rows]

    async def count(self) -> int:
        """返回总行数。"""
        async with self._connect() as db:
            _ = self._table_sql
            cursor = await db.execute("SELECT COUNT(*) FROM social_relations")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def list_group_ids(self) -> list[str]:
        """返回社交关系表中所有非空且去重的群组 ID。"""
        async with self._connect() as db:
            _ = self._table_sql
            cursor = await db.execute(
                """
                SELECT DISTINCT group_id
                FROM social_relations
                WHERE group_id <> ''
                ORDER BY group_id
                """
            )
            rows = await cursor.fetchall()
            return [str(row[0]) for row in rows if row and row[0]]


__all__ = ["RelationStore", "SOCIAL_SORT_COLUMNS"]
