"""带版本历史的笔记 SQLite 存储实现。"""

from __future__ import annotations

import json
import time
from typing import Any

from ....models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ...memory.infrastructure.base import BaseStore
from ...memory.infrastructure.domain_object_integrity import (
    filter_current_domain_objects,
    validate_domain_object_write,
)
from ..domain.models import Note, NoteStatus, NoteVersion

_CREATE_NOTES = """CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, content TEXT NOT NULL,
    tags TEXT DEFAULT '[]', status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    user_id TEXT DEFAULT '', source_memory_ids TEXT DEFAULT '[]',
    origin TEXT DEFAULT 'manual', provenance_json TEXT
)"""
_CREATE_VERSIONS = """CREATE TABLE IF NOT EXISTS note_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL, version INTEGER NOT NULL,
    content TEXT NOT NULL, created_at REAL NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(id)
)"""
_NOTE_MIGRATIONS = {
    "origin": "ALTER TABLE notes ADD COLUMN origin TEXT DEFAULT 'manual'",
    "provenance_json": "ALTER TABLE notes ADD COLUMN provenance_json TEXT",
}


class NoteStore(BaseStore):
    """持久化版本化笔记并维护 derived 来源可见性。"""

    def __init__(self, db_path: str) -> None:
        """保存 SQLite 路径。"""

        self.db_path = db_path

    async def init_table(self) -> None:
        """创建笔记表、版本表和来源迁移字段。"""

        async with self._connect() as db:
            await db.execute(_CREATE_NOTES)
            await db.execute(_CREATE_VERSIONS)
            cursor = await db.execute("PRAGMA table_info(notes)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            for column, migration_sql in _NOTE_MIGRATIONS.items():
                if column not in columns:
                    await db.execute(migration_sql)
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_note_versions_note_version
                ON note_versions(note_id, version)
                """
            )
            await db.commit()

    async def create(self, note: Note) -> int:
        """校验来源后创建笔记及首个版本，派生 provenance 保持幂等。"""

        now = time.time()
        note.created_at = now
        note.updated_at = now
        note.version = 1
        provenance_json = (
            self._to_json(note.provenance.to_dict())
            if note.provenance is not None
            else None
        )
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await validate_domain_object_write(
                    db,
                    note.origin,
                    note.provenance,
                )
                if note.origin is DomainObjectOrigin.DERIVED:
                    cursor = await db.execute(
                        """SELECT id FROM notes
                           WHERE origin = ? AND provenance_json = ? AND status != ?
                           ORDER BY id ASC LIMIT 1""",
                        (
                            DomainObjectOrigin.DERIVED.value,
                            provenance_json,
                            NoteStatus.DELETED.value,
                        ),
                    )
                    existing = await cursor.fetchone()
                    if existing is not None:
                        await db.rollback()
                        return int(existing[0])
                cursor = await db.execute(
                    """INSERT INTO notes (title, content, tags, status, version,
                   created_at, updated_at, user_id, source_memory_ids,
                   origin, provenance_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        note.origin.value,
                        provenance_json,
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
            except BaseException:
                await db.rollback()
                raise

    async def get(self, note_id: int) -> Note | None:
        """读取单条笔记，并过滤 stale derived 对象。"""

        async with self._connect() as db:
            cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
            row = await cursor.fetchone()
            note = self._row_to_note(row) if row else None
        if note is None:
            return None
        visible = await self.filter_current_sources([note])
        return visible[0] if visible else None

    async def update(self, note: Note) -> bool:
        """按乐观版本更新笔记并追加版本快照。"""

        previous_version = int(note.version or 1)
        next_version = previous_version + 1
        note.updated_at = time.time()
        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await validate_domain_object_write(
                    db,
                    note.origin,
                    note.provenance,
                )
                cursor = await db.execute(
                    """UPDATE notes SET title=?, content=?, tags=?, status=?,
                   version=?, updated_at=?, source_memory_ids=?, origin=?,
                   provenance_json=? WHERE id=? AND version=?""",
                    (
                        note.title,
                        note.content,
                        json.dumps(note.tags),
                        note.status.value,
                        next_version,
                        note.updated_at,
                        json.dumps(note.source_memory_ids),
                        note.origin.value,
                        (
                            self._to_json(note.provenance.to_dict())
                            if note.provenance is not None
                            else None
                        ),
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
            except BaseException:
                await db.rollback()
                raise

    async def soft_delete(self, note_id: int) -> bool:
        """将笔记标记为已删除，同时保留版本历史。"""

        async with self._connect() as db:
            cursor = await db.execute(
                """UPDATE notes SET status = ?, updated_at = ?
                   WHERE id = ? AND status != ?""",
                (
                    NoteStatus.DELETED.value,
                    time.time(),
                    note_id,
                    NoteStatus.DELETED.value,
                ),
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
        """搜索未删除笔记，并过滤 stale derived 对象。"""

        like = f"%{query}%"
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT * FROM notes WHERE (title LIKE ? OR content LIKE ?)
                   AND status != 'deleted' ORDER BY updated_at DESC""",
                (like, like),
            )
            rows = await cursor.fetchall()
            notes = [self._row_to_note(r) for r in rows]
            visible = await filter_current_domain_objects(db, notes)
        return visible[:limit], len(visible)

    async def filter_current_sources(self, notes: list[Note]) -> list[Note]:
        """读取时丢弃 stale derived note，保留人工笔记。"""

        if not notes:
            return []
        async with self._connect() as db:
            return await filter_current_domain_objects(db, notes)

    async def list_notes(
        self, limit: int = 50, offset: int = 0, status: str = ""
    ) -> tuple[list[Note], int]:
        """分页列出笔记，并过滤 stale derived 对象。"""

        async with self._connect() as db:
            if status:
                cursor = await db.execute(
                    """SELECT * FROM notes WHERE status = ?
                       ORDER BY updated_at DESC""",
                    (status,),
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM notes WHERE status != ?
                       ORDER BY updated_at DESC""",
                    (NoteStatus.DELETED.value,),
                )
            rows = await cursor.fetchall()
            notes = [self._row_to_note(r) for r in rows]
            visible = await filter_current_domain_objects(db, notes)
        return visible[offset : offset + limit], len(visible)

    async def get_versions(self, note_id: int) -> list[NoteVersion]:
        """按版本倒序返回笔记历史。"""

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
        """返回未删除笔记总数。"""

        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM notes WHERE status != 'deleted'"
            )
            return (await cursor.fetchone())[0]

    async def prune_versions(self, max_versions: int = 20) -> int:
        """为每条笔记仅保留指定数量的最新版本。"""

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
        """把数据库行转换为兼容旧字段的笔记模型。"""

        provenance_data = (
            BaseStore._from_json(row[11]) if len(row) > 11 and row[11] else None
        )
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
            origin=DomainObjectOrigin(str(row[10] or "manual"))
            if len(row) > 10
            else DomainObjectOrigin.MANUAL,
            provenance=(
                DomainProvenance.from_dict(provenance_data)
                if isinstance(provenance_data, dict)
                else None
            ),
        )


__all__ = ["NoteStore"]
