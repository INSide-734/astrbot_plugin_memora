"""知识条目的 SQLite 存储，支持基于 LIKE 的搜索。"""

from __future__ import annotations

import json
import time
from typing import Any

from ..base.list_sorting import SortQuery, order_by_clause
from ..models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ..models.knowledge_models import KnowledgeEntry, KnowledgeType
from .base import BaseStore
from .domain_object_integrity import (
    filter_current_domain_objects,
    validate_domain_object_write,
)


KNOWLEDGE_SORT_COLUMNS = {
    "title": "title COLLATE NOCASE",
    "category": "category COLLATE NOCASE",
    "confidence": "confidence",
    "updated_at": "updated_at",
    "access_count": "access_count",
}
_KNOWLEDGE_SQL_COLUMNS = {**KNOWLEDGE_SORT_COLUMNS, "id": "id"}

_CREATE_KB = """CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'fact',
    confidence REAL DEFAULT 0.5,
    source_ids TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL DEFAULT 0,
    access_count INTEGER DEFAULT 0,
    origin TEXT DEFAULT 'manual',
    provenance_json TEXT
)"""


class KnowledgeStore(BaseStore):
    """持久化结构化知识条目并维护 derived 来源可见性。"""

    def __init__(self, db_path: str) -> None:
        """保存 SQLite 路径。"""

        self.db_path = db_path

    async def init_table(self) -> None:
        """创建知识表并幂等补齐来源字段。"""

        async with self._connect() as db:
            await db.execute(_CREATE_KB)
            cursor = await db.execute("PRAGMA table_info(knowledge_entries)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            for column, definition in (
                ("origin", "TEXT DEFAULT 'manual'"),
                ("provenance_json", "TEXT"),
            ):
                if column not in columns:
                    await db.execute(
                        f"ALTER TABLE knowledge_entries ADD COLUMN {column} {definition}"
                    )
            await db.commit()

    async def insert(self, entry: KnowledgeEntry) -> int:
        """校验来源后插入知识条目并返回内部 ID。"""

        now = time.time()
        entry.created_at = now
        entry.updated_at = now
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await validate_domain_object_write(
                    db,
                    entry.origin,
                    entry.provenance,
                )
                cursor = await db.execute(
                """INSERT INTO knowledge_entries
                   (title, content, category, confidence, source_ids, tags,
                    created_at, updated_at, expires_at, origin, provenance_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.title,
                    entry.content,
                    entry.category.value,
                    entry.confidence,
                    json.dumps(entry.source_ids),
                    json.dumps(entry.tags),
                    now,
                    now,
                    entry.expires_at,
                    entry.origin.value,
                    (
                        self._to_json(entry.provenance.to_dict())
                        if entry.provenance is not None
                        else None
                    ),
                ),
                )
                await db.commit()
                return cursor.lastrowid
            except BaseException:
                await db.rollback()
                raise

    async def get(self, entry_id: int) -> KnowledgeEntry | None:
        """读取单条知识，并过滤 stale derived 对象。"""

        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
            )
            row = await cursor.fetchone()
            entry = self._row_to_entry(row) if row else None
        if entry is None:
            return None
        visible = await self.filter_current_sources([entry])
        return visible[0] if visible else None

    async def search(
        self,
        query: str,
        limit: int = 20,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """按关键词搜索知识，并过滤不可见的 derived 条目。"""

        like = f"%{query}%"
        order_by = order_by_clause(
            sort,
            columns=_KNOWLEDGE_SQL_COLUMNS,
            tie_breaker="id",
        )
        async with self._connect() as db:
            if category:
                cursor = await db.execute(
                    f"""SELECT * FROM knowledge_entries
                       WHERE category = ? AND (title LIKE ? OR content LIKE ?)
                       ORDER BY {order_by}""",
                    (category, like, like),
                )
            else:
                cursor = await db.execute(
                    f"""SELECT * FROM knowledge_entries
                       WHERE title LIKE ? OR content LIKE ?
                       ORDER BY {order_by}""",
                    (like, like),
                )
            rows = await cursor.fetchall()
            entries = [self._row_to_entry(r) for r in rows]
            visible = await filter_current_domain_objects(db, entries)
        return visible[:limit], len(visible)

    async def search_merge_candidates(
        self,
        query: str,
        limit: int = 5,
    ) -> list[KnowledgeEntry]:
        """返回去重恢复候选，允许包含待读写事务重新校验的 stale 条目。"""

        like = f"%{query}%"
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT * FROM knowledge_entries
                   WHERE title LIKE ? OR content LIKE ?
                   ORDER BY updated_at DESC, id DESC LIMIT ?""",
                (like, like, limit),
            )
            rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """分页列出知识，并过滤不可见的 derived 条目。"""

        order_by = order_by_clause(
            sort,
            columns=_KNOWLEDGE_SQL_COLUMNS,
            tie_breaker="id",
        )
        async with self._connect() as db:
            if category:
                cursor = await db.execute(
                    f"""SELECT * FROM knowledge_entries
                       WHERE category = ? ORDER BY {order_by}""",
                    (category,),
                )
            else:
                cursor = await db.execute(
                    f"SELECT * FROM knowledge_entries ORDER BY {order_by}",
                )
            rows = await cursor.fetchall()
            entries = [self._row_to_entry(r) for r in rows]
            visible = await filter_current_domain_objects(db, entries)
        return visible[offset : offset + limit], len(visible)

    async def filter_current_sources(
        self,
        entries: list[KnowledgeEntry],
    ) -> list[KnowledgeEntry]:
        """读取时丢弃 stale derived knowledge，保留人工条目。"""

        if not entries:
            return []
        async with self._connect() as db:
            return await filter_current_domain_objects(db, entries)

    async def update(self, entry: KnowledgeEntry) -> None:
        """重新校验来源后更新知识条目。"""

        entry.updated_at = time.time()
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await validate_domain_object_write(
                    db,
                    entry.origin,
                    entry.provenance,
                )
                await db.execute(
                """UPDATE knowledge_entries SET title=?, content=?, category=?,
                   confidence=?, tags=?, source_ids=?, updated_at=?, access_count=?,
                   origin=?, provenance_json=? WHERE id=?""",
                (
                    entry.title,
                    entry.content,
                    entry.category.value,
                    entry.confidence,
                    json.dumps(entry.tags),
                    json.dumps(entry.source_ids),
                    entry.updated_at,
                    entry.access_count,
                    entry.origin.value,
                    (
                        self._to_json(entry.provenance.to_dict())
                        if entry.provenance is not None
                        else None
                    ),
                    entry.entry_id,
                ),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def delete(self, entry_id: int) -> bool:
        """按内部 ID 删除知识条目。"""

        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM knowledge_entries WHERE id = ?", (entry_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def count(self) -> int:
        """返回知识条目总数。"""

        async with self._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM knowledge_entries")
            return (await cursor.fetchone())[0]

    @staticmethod
    def _row_to_entry(row: Any) -> KnowledgeEntry:
        """把数据库行转换为兼容旧字段的知识模型。"""

        provenance_data = (
            BaseStore._from_json(row[12]) if len(row) > 12 and row[12] else None
        )
        return KnowledgeEntry(
            title=str(row[1] or ""),
            content=str(row[2] or ""),
            category=KnowledgeType(str(row[3] or "fact")),
            confidence=float(row[4] or 0.5),
            source_ids=json.loads(row[5]) if row[5] else [],
            tags=json.loads(row[6]) if row[6] else [],
            created_at=float(row[7] or 0),
            updated_at=float(row[8] or 0),
            expires_at=float(row[9] or 0),
            access_count=int(row[10] or 0),
            entry_id=int(row[0] or 0),
            origin=DomainObjectOrigin(str(row[11] or "manual"))
            if len(row) > 11
            else DomainObjectOrigin.MANUAL,
            provenance=(
                DomainProvenance.from_dict(provenance_data)
                if isinstance(provenance_data, dict)
                else None
            ),
        )


__all__ = ["KNOWLEDGE_SORT_COLUMNS", "KnowledgeStore"]
