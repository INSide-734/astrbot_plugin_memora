"""知识条目的 SQLite 存储，支持基于 LIKE 的搜索。"""

from __future__ import annotations

import json
import time
from typing import Any

from ..base.list_sorting import SortQuery, order_by_clause
from ..models.knowledge_models import KnowledgeEntry, KnowledgeType
from .base import BaseStore


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
    access_count INTEGER DEFAULT 0
)"""


class KnowledgeStore(BaseStore):
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_table(self) -> None:
        async with self._connect() as db:
            await db.execute(_CREATE_KB)
            await db.commit()

    async def insert(self, entry: KnowledgeEntry) -> int:
        now = time.time()
        entry.created_at = now
        entry.updated_at = now
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO knowledge_entries
                   (title, content, category, confidence, source_ids, tags,
                    created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get(self, entry_id: int) -> KnowledgeEntry | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
            )
            row = await cursor.fetchone()
            return self._row_to_entry(row) if row else None

    async def search(
        self,
        query: str,
        limit: int = 20,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
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
                       ORDER BY {order_by} LIMIT ?""",
                    (category, like, like, limit),
                )
            else:
                cursor = await db.execute(
                    f"""SELECT * FROM knowledge_entries
                       WHERE title LIKE ? OR content LIKE ?
                       ORDER BY {order_by} LIMIT ?""",
                    (like, like, limit),
                )
            rows = await cursor.fetchall()
            cursor2 = await db.execute("SELECT COUNT(*) FROM knowledge_entries")
            total = (await cursor2.fetchone())[0]
        return [self._row_to_entry(r) for r in rows], total

    async def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        order_by = order_by_clause(
            sort,
            columns=_KNOWLEDGE_SQL_COLUMNS,
            tie_breaker="id",
        )
        async with self._connect() as db:
            if category:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM knowledge_entries WHERE category = ?",
                    (category,),
                )
                total = (await cursor.fetchone())[0]
                cursor = await db.execute(
                    f"""SELECT * FROM knowledge_entries
                       WHERE category = ? ORDER BY {order_by} LIMIT ? OFFSET ?""",
                    (category, limit, offset),
                )
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM knowledge_entries")
                total = (await cursor.fetchone())[0]
                cursor = await db.execute(
                    f"SELECT * FROM knowledge_entries ORDER BY {order_by} LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = await cursor.fetchall()
        return [self._row_to_entry(r) for r in rows], total

    async def update(self, entry: KnowledgeEntry) -> None:
        entry.updated_at = time.time()
        async with self._connect() as db:
            await db.execute(
                """UPDATE knowledge_entries SET title=?, content=?, category=?,
                   confidence=?, tags=?, updated_at=?, access_count=? WHERE id=?""",
                (
                    entry.title,
                    entry.content,
                    entry.category.value,
                    entry.confidence,
                    json.dumps(entry.tags),
                    entry.updated_at,
                    entry.access_count,
                    entry.entry_id,
                ),
            )
            await db.commit()

    async def delete(self, entry_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM knowledge_entries WHERE id = ?", (entry_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def count(self) -> int:
        async with self._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM knowledge_entries")
            return (await cursor.fetchone())[0]

    @staticmethod
    def _row_to_entry(row: Any) -> KnowledgeEntry:
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
        )


__all__ = ["KNOWLEDGE_SORT_COLUMNS", "KnowledgeStore"]
