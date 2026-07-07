"""带版本历史的笔记 SQLite 存储实现。"""

from __future__ import annotations

import json
import time
from typing import Any

from ..models.note_models import Note, NoteStatus, NoteVersion
from .base import BaseStore

_CREATE_NOTES = """CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, content TEXT NOT NULL,
    tags TEXT DEFAULT '[]', status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    user_id TEXT DEFAULT '', source_memory_ids TEXT DEFAULT '[]'
)"""
_CREATE_VERSIONS = """CREATE TABLE IF NOT EXISTS note_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL, version INTEGER NOT NULL,
    content TEXT NOT NULL, created_at REAL NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(id)
)"""


class NoteStore(BaseStore):
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_table(self) -> None:
        async with self._connect() as db:
            await db.execute(_CREATE_NOTES)
            await db.execute(_CREATE_VERSIONS)
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_note_versions_note_version
                ON note_versions(note_id, version)
                """
            )
            await db.commit()

    async def create(self, note: Note) -> int:
        now = time.time()
        note.created_at = now
        note.updated_at = now
        note.version = 1
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO notes (title, content, tags, status, version,
                   created_at, updated_at, user_id, source_memory_ids)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    note.title,
                    note.content,
                    json.dumps(note.tags),
                    note.status.value,
                    1,
                    now,
                    now,
                    note.user_id,
                    json.dumps(note.source_memory_ids),
                ),
            )
            note_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO note_versions (note_id, version, content, created_at)
                   VALUES (?, ?, ?, ?)""",
                (note_id, 1, note.content, now),
            )
            await db.commit()
            return note_id

    async def get(self, note_id: int) -> Note | None:
        async with self._connect() as db:
            cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
            row = await cursor.fetchone()
            return self._row_to_note(row) if row else None

    async def update(self, note: Note) -> bool:
        previous_version = int(note.version or 1)
        next_version = previous_version + 1
        note.updated_at = time.time()
        async with self._connect() as db:
            cursor = await db.execute(
                """UPDATE notes SET title=?, content=?, tags=?, status=?,
                   version=?, updated_at=? WHERE id=? AND version=?""",
                (
                    note.title,
                    note.content,
                    json.dumps(note.tags),
                    note.status.value,
                    next_version,
                    note.updated_at,
                    note.note_id,
                    previous_version,
                ),
            )
            if cursor.rowcount <= 0:
                await db.rollback()
                return False
            await db.execute(
                """INSERT INTO note_versions (note_id, version, content, created_at)
                   VALUES (?, ?, ?, ?)""",
                (note.note_id, next_version, note.content, note.updated_at),
            )
            await db.commit()
            note.version = next_version
            return True

    async def soft_delete(self, note_id: int) -> bool:
        """将笔记标记为已删除，同时保留版本历史。"""
        async with self._connect() as db:
            cursor = await db.execute(
                """UPDATE notes SET status = ?, updated_at = ?
                   WHERE id = ? AND status != ?""",
                (NoteStatus.DELETED.value, time.time(), note_id, NoteStatus.DELETED.value),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete(self, note_id: int) -> bool:
        """彻底删除笔记及其版本历史。"""
        async with self._connect() as db:
            await db.execute("DELETE FROM note_versions WHERE note_id = ?", (note_id,))
            cursor = await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def search(self, query: str, limit: int = 20) -> tuple[list[Note], int]:
        like = f"%{query}%"
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT * FROM notes WHERE (title LIKE ? OR content LIKE ?)
                   AND status != 'deleted' ORDER BY updated_at DESC LIMIT ?""",
                (like, like, limit),
            )
            rows = await cursor.fetchall()
            cursor2 = await db.execute(
                "SELECT COUNT(*) FROM notes WHERE status != 'deleted'"
            )
            total = (await cursor2.fetchone())[0]
        return [self._row_to_note(r) for r in rows], total

    async def list_notes(
        self, limit: int = 50, offset: int = 0, status: str = ""
    ) -> tuple[list[Note], int]:
        async with self._connect() as db:
            if status:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM notes WHERE status = ?", (status,)
                )
                total = (await cursor.fetchone())[0]
                cursor = await db.execute(
                    """SELECT * FROM notes WHERE status = ?
                       ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                    (status, limit, offset),
                )
            else:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM notes WHERE status != ?",
                    (NoteStatus.DELETED.value,),
                )
                total = (await cursor.fetchone())[0]
                cursor = await db.execute(
                    """SELECT * FROM notes WHERE status != ?
                       ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                    (NoteStatus.DELETED.value, limit, offset),
                )
            rows = await cursor.fetchall()
        return [self._row_to_note(r) for r in rows], total

    async def get_versions(self, note_id: int) -> list[NoteVersion]:
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT version, content, created_at FROM note_versions
                   WHERE note_id = ? ORDER BY version DESC""",
                (note_id,),
            )
            rows = await cursor.fetchall()
        return [
            NoteVersion(
                version=int(r[0]), content=str(r[1] or ""), created_at=float(r[2] or 0)
            )
            for r in rows
        ]

    async def count(self) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM notes WHERE status != 'deleted'"
            )
            return (await cursor.fetchone())[0]

    async def prune_versions(self, max_versions: int = 20) -> int:
        removed = 0
        async with self._connect() as db:
            cursor = await db.execute("SELECT id FROM notes")
            note_ids = [row[0] for row in await cursor.fetchall()]
            for note_id in note_ids:
                cursor2 = await db.execute(
                    "SELECT COUNT(*) FROM note_versions WHERE note_id = ?", (note_id,)
                )
                count = (await cursor2.fetchone())[0]
                if count > max_versions:
                    excess = count - max_versions
                    await db.execute(
                        "DELETE FROM note_versions WHERE id IN ("
                        "SELECT id FROM note_versions WHERE note_id = ? "
                        "ORDER BY created_at ASC LIMIT ?)",
                        (note_id, excess),
                    )
                    removed += excess
            if removed:
                await db.commit()
        return removed

    @staticmethod
    def _row_to_note(row: Any) -> Note:
        return Note(
            title=str(row[1] or ""),
            content=str(row[2] or ""),
            tags=json.loads(row[3]) if row[3] else [],
            status=NoteStatus(str(row[4] or "active")),
            version=int(row[5] or 1),
            created_at=float(row[6] or 0),
            updated_at=float(row[7] or 0),
            note_id=int(row[0] or 0),
            user_id=str(row[8] or ""),
            source_memory_ids=json.loads(row[9]) if row[9] else [],
        )


__all__ = ["NoteStore"]
