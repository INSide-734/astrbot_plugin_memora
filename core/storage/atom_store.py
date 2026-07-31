"""带时间属性的记忆原子 SQLite 存储层。"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite
from astrbot.api import logger

from ..models.memory_atom import (
    AtomStatus,
    AtomType,
    DecayType,
    MemoryAtom,
    compute_ttl,
)
from .atom_fts import AtomFTSMixin
from .atom_source_integrity import (
    filter_atoms_by_current_sources,
    validate_atom_parent_sources,
)
from .base import BaseStore


class AtomStore(BaseStore, AtomFTSMixin):
    """持久化记忆原子，并提供 FTS 检索支持。"""

    _SQLITE_BATCH_SIZE = 500
    _PARENT_COLUMN_MIGRATIONS = {
        "parent_revision": "ALTER TABLE memory_atoms ADD COLUMN parent_revision TEXT",
        "parent_scope_key": "ALTER TABLE memory_atoms ADD COLUMN parent_scope_key TEXT",
        "parent_privacy_level": (
            "ALTER TABLE memory_atoms ADD COLUMN parent_privacy_level TEXT"
        ),
    }

    def __init__(self, db_path: str, config: dict[str, Any] | None = None):
        """保存 SQLite 路径与原子生命周期配置。"""

        self.db_path = db_path
        self.config = config or {}

    async def initialize(self) -> None:
        """创建记忆原子相关数据表。"""
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_atoms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_memory_id INTEGER NOT NULL,
                    parent_revision TEXT,
                    parent_scope_key TEXT,
                    parent_privacy_level TEXT,
                    atom_type TEXT NOT NULL DEFAULT 'unknown',
                    content TEXT NOT NULL,
                    entities TEXT DEFAULT '[]',
                    importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    last_reinforced_at REAL,
                    event_time REAL,
                    ttl_days REAL NOT NULL DEFAULT 30.0,
                    expires_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    reinforcement_count INTEGER NOT NULL DEFAULT 0,
                    decay_type TEXT NOT NULL DEFAULT 'exponential',
                    session_id TEXT,
                    persona_id TEXT,
                    metadata TEXT DEFAULT '{}'
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_parent ON memory_atoms(parent_memory_id)"
            )
            cursor = await db.execute("PRAGMA table_info(memory_atoms)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            for column in (
                "parent_revision",
                "parent_scope_key",
                "parent_privacy_level",
            ):
                if column not in columns:
                    await db.execute(self._PARENT_COLUMN_MIGRATIONS[column])
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_status ON memory_atoms(status)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_expires ON memory_atoms(expires_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_session ON memory_atoms(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_atoms_persona ON memory_atoms(persona_id)"
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_atoms_scope_status
                ON memory_atoms(status, session_id, persona_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_atoms_status_expires
                ON memory_atoms(status, expires_at)
                """
            )
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_atoms_fts
                USING fts5(content, atom_id UNINDEXED, tokenize='unicode61')
                """
            )
            await db.commit()

    async def insert(self, atom: MemoryAtom) -> int:
        """插入新原子并返回其 ID，同时原地更新 ``atom.atom_id``。"""
        self._prepare_atom_for_insert(atom)

        async with self._connect() as db:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await validate_atom_parent_sources(db, [atom])
                atom_id = await self._insert_atom(db, atom)
                await db.commit()
            except BaseException:
                await db.rollback()
                atom.atom_id = 0
                raise
        return atom_id

    async def insert_many(self, atoms: list[MemoryAtom]) -> list[int]:
        """按分块事务批量插入原子，并返回对应 ID 列表。"""
        if not atoms:
            return []

        atom_ids: list[int] = []
        async with self._connect() as db:
            for index in range(0, len(atoms), self._SQLITE_BATCH_SIZE):
                batch = atoms[index : index + self._SQLITE_BATCH_SIZE]
                batch_atom_ids: list[int] = []
                prepared_batch: list[MemoryAtom] = []
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    for atom in batch:
                        self._prepare_atom_for_insert(atom)
                        prepared_batch.append(atom)
                    await validate_atom_parent_sources(db, prepared_batch)
                    for atom in prepared_batch:
                        batch_atom_ids.append(await self._insert_atom(db, atom))
                    await db.commit()
                except Exception:
                    await db.rollback()
                    for atom in prepared_batch:
                        atom.atom_id = 0
                    raise
                atom_ids.extend(batch_atom_ids)
        return atom_ids

    def _prepare_atom_for_insert(self, atom: MemoryAtom) -> None:
        """在持久化前补齐基于时间推导的字段。"""
        now = time.time()
        atom.created_at = now
        atom.last_accessed_at = now
        metadata = dict(atom.metadata or {})
        metadata.setdefault("emotion_tags", list(atom.emotion_tags))
        atom.metadata = metadata
        persona_modifier = float(metadata.get("persona_decay_modifier", 1.0))
        emotional_intensity = max(
            0.0,
            min(1.0, float(metadata.get("emotional_intensity", 0.5))),
        )
        ttl, decay = compute_ttl(
            atom.atom_type,
            atom.importance,
            atom.reinforcement_count,
            atom.event_time,
            emotional_intensity=emotional_intensity,
            persona_decay_modifier=persona_modifier,
            allow_probationary=bool(self.config.get("atom_probationary_enabled", True)),
            probationary_ttl_days=float(
                self.config.get("atom_probationary_ttl_days", 3.0)
            ),
        )
        atom.ttl_days = ttl
        atom.decay_type = decay
        atom.expires_at = now + ttl * 86400.0

    async def _insert_atom(
        self,
        db: aiosqlite.Connection,
        atom: MemoryAtom,
    ) -> int:
        """在调用方事务中写入 Atom 主表与 FTS 行。"""

        cursor = await db.execute(
            """
            INSERT INTO memory_atoms (
                parent_memory_id, parent_revision, parent_scope_key,
                parent_privacy_level, atom_type, content, entities,
                importance, confidence, created_at, last_accessed_at,
                last_reinforced_at, event_time, ttl_days, expires_at,
                status, reinforcement_count, decay_type,
                session_id, persona_id, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                atom.parent_memory_id,
                atom.parent_revision,
                atom.parent_scope_key,
                atom.parent_privacy_level,
                atom.atom_type.value,
                atom.content,
                json.dumps(atom.entities, ensure_ascii=False),
                atom.importance,
                atom.confidence,
                atom.created_at,
                atom.last_accessed_at,
                atom.last_reinforced_at,
                atom.event_time,
                atom.ttl_days,
                atom.expires_at,
                atom.status.value,
                atom.reinforcement_count,
                atom.decay_type.value,
                atom.session_id,
                atom.persona_id,
                self._to_json(atom.metadata),
            ),
        )
        atom_id = int(cursor.lastrowid)
        atom.atom_id = atom_id

        await db.execute(
            "INSERT INTO memory_atoms_fts(atom_id, content) VALUES (?, ?)",
            (atom_id, atom.content),
        )
        return atom_id

    async def get(self, atom_id: int) -> MemoryAtom | None:
        """按 ID 获取仍绑定当前 canonical source 的原子。"""

        atom = await self.get_raw(atom_id)
        if atom is None:
            return None
        current = await self.filter_current_sources([atom])
        return current[0] if current else None

    async def get_raw(self, atom_id: int) -> MemoryAtom | None:
        """按 ID 读取未经父来源校验的原子，仅供维护和修复使用。"""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memory_atoms WHERE id = ?", (atom_id,)
            )
            row = await cursor.fetchone()
        return self._row_to_atom(row) if row else None

    async def get_by_parent(self, parent_memory_id: int) -> list[MemoryAtom]:
        """获取仍绑定当前 canonical source 的全部子原子。"""

        atoms = await self.get_by_parent_raw(parent_memory_id)
        return await self.filter_current_sources(atoms)

    async def get_by_parent_raw(self, parent_memory_id: int) -> list[MemoryAtom]:
        """读取父记忆下未经来源校验的原子，仅供维护和修复去重。"""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memory_atoms WHERE parent_memory_id = ? ORDER BY id ASC",
                (parent_memory_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_atom(row) for row in rows]

    async def filter_current_sources(self, atoms: list[MemoryAtom]) -> list[MemoryAtom]:
        """按父 canonical 当前 revision、scope 与 privacy 过滤原子。"""

        if not atoms:
            return []
        async with self._connect() as db:
            return await filter_atoms_by_current_sources(db, atoms)

    async def update_status(self, atom_id: int, status: AtomStatus) -> bool:
        """更新单个原子的生命周期状态。"""
        async with self._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET status = ? WHERE id = ?",
                (status.value, atom_id),
            )
            await db.commit()
        return True

    async def touch(self, atom_id: int) -> None:
        """更新原子的 ``last_accessed_at``。"""
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET last_accessed_at = ? WHERE id = ?",
                (now, atom_id),
            )
            await db.commit()

    async def touch_many(self, atom_ids: list[int]) -> None:
        """在单个事务中更新多条原子的访问时间，并忽略重复或非法 ID。"""

        normalized_ids = sorted({int(atom_id) for atom_id in atom_ids if atom_id > 0})
        if not normalized_ids:
            return
        now = time.time()
        async with self._connect() as db:
            await db.executemany(
                "UPDATE memory_atoms SET last_accessed_at = ? WHERE id = ?",
                [(now, atom_id) for atom_id in normalized_ids],
            )
            await db.commit()

    async def reinforce(
        self, atom_id: int, new_confidence: float | None = None
    ) -> None:
        """记录一次强化事件，延长 TTL，并可选择提升置信度。"""
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT reinforcement_count, importance, confidence, atom_type, event_time, metadata FROM memory_atoms WHERE id = ?",
                (atom_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return

            new_count = int(row["reinforcement_count"]) + 1
            importance = float(row["importance"])
            atom_type = AtomType(row["atom_type"])
            event_time = float(row["event_time"]) if row["event_time"] else None
            # 人格调制遗忘率
            raw_meta = row["metadata"]
            meta_dict = self._from_json(raw_meta) if raw_meta else {}
            persona_mod = float(meta_dict.get("persona_decay_modifier", 1.0))
            emotional_intensity = max(
                0.0,
                min(1.0, float(meta_dict.get("emotional_intensity", 0.5))),
            )
            new_ttl, decay = compute_ttl(
                atom_type,
                importance,
                new_count,
                event_time,
                emotional_intensity=emotional_intensity,
                persona_decay_modifier=persona_mod,
                allow_probationary=bool(
                    self.config.get("atom_probationary_enabled", True)
                ),
                probationary_ttl_days=float(
                    self.config.get("atom_probationary_ttl_days", 3.0)
                ),
            )

            confidence = (
                new_confidence
                if new_confidence is not None
                else float(row["confidence"])
            )
            # 若提供新置信度，则按 EMA 方式更新
            if new_confidence is not None:
                confidence = float(row["confidence"]) * 0.7 + new_confidence * 0.3

            await db.execute(
                """
                UPDATE memory_atoms
                SET reinforcement_count = ?, confidence = ?,
                    ttl_days = ?, expires_at = ?, decay_type = ?,
                    last_reinforced_at = ?
                WHERE id = ?
                """,
                (
                    new_count,
                    confidence,
                    new_ttl,
                    now + new_ttl * 86400.0,
                    decay.value,
                    now,
                    atom_id,
                ),
            )
            await db.commit()

    async def expire_stale_atoms(self) -> int:
        """将已过期的原子标记为 EXPIRED，并返回数量。"""
        now = time.time()
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE memory_atoms SET status = ? WHERE status = 'active' AND expires_at < ?",
                (AtomStatus.EXPIRED.value, now),
            )
            await db.commit()
            return cursor.rowcount

    async def cleanup_forgotten(
        self, older_than_days: float = 7.0, batch_size: int = 500
    ) -> int:
        """清理超过阈值的 FORGOTTEN 原子及其 FTS 索引。"""
        cutoff = time.time() - older_than_days * 86400.0
        total_removed = 0
        async with self._connect() as db:
            while True:
                cursor = await db.execute(
                    "SELECT id FROM memory_atoms "
                    "WHERE status = 'forgotten' AND expires_at < ? "
                    "LIMIT ?",
                    (cutoff, batch_size),
                )
                rows = await cursor.fetchall()
                atom_ids = [int(row[0]) for row in rows]
                if not atom_ids:
                    break
                await db.execute(
                    """
                    DELETE FROM memory_atoms_fts
                    WHERE atom_id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                await db.execute(
                    """
                    DELETE FROM memory_atoms
                    WHERE id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                total_removed += len(atom_ids)
            await db.commit()
        return total_removed

    async def forget_expired_atoms(
        self, older_than_days: float = 7.0, batch_size: int = 500
    ) -> int:
        """将陈旧的 EXPIRED 原子软删除，并移出 FTS 索引。"""
        cutoff = time.time() - older_than_days * 86400.0
        total_forgotten = 0
        async with self._connect() as db:
            while True:
                cursor = await db.execute(
                    "SELECT id FROM memory_atoms "
                    "WHERE status = 'expired' AND expires_at < ? "
                    "LIMIT ?",
                    (cutoff, batch_size),
                )
                rows = await cursor.fetchall()
                atom_ids = [int(row[0]) for row in rows]
                if not atom_ids:
                    break
                await db.execute(
                    """
                    DELETE FROM memory_atoms_fts
                    WHERE atom_id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                await db.execute(
                    """
                    UPDATE memory_atoms SET status = :status
                    WHERE id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {
                        "status": AtomStatus.FORGOTTEN.value,
                        "atom_ids_json": json.dumps(atom_ids),
                    },
                )
                total_forgotten += len(atom_ids)
            await db.commit()
        return total_forgotten

    async def migrate_to_cold(
        self,
        cold_days_threshold: float = 14.0,
        max_importance: float = 0.4,
    ) -> int:
        """v2.6: 将长期未被访问的低重要性原子迁移到冷存储 (COLD 状态)。

        COLD 原子不参与常规 FTS 检索；当前不提供自动复活路径。

        Args:
            cold_days_threshold: 最后一次访问距今超过此天数触发迁移（默认 14 天）。
            max_importance: 仅重要性低于此值的原子参与迁移（默认 0.4）。

        Returns:
            迁移的原子数量。
        """
        threshold_time = time.time() - cold_days_threshold * 86400.0
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE memory_atoms SET status = ? "
                "WHERE status = 'active' "
                "AND last_accessed_at < ? "
                "AND importance < ?",
                (AtomStatus.COLD.value, threshold_time, max_importance),
            )
            await db.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(
                    f"[AtomStore] 冷存储迁移: {count} 个原子 → COLD "
                    f"(阈值: {cold_days_threshold}天未访问, importance < {max_importance})"
                )
            return count

    async def delete_by_parent(
        self, parent_memory_id: int, batch_size: int = 500
    ) -> int:
        """删除某个父记忆下的全部原子，并返回删除数量。"""
        total_deleted = 0
        async with self._connect() as db:
            while True:
                cursor = await db.execute(
                    "SELECT id FROM memory_atoms WHERE parent_memory_id = ? LIMIT ?",
                    (parent_memory_id, batch_size),
                )
                rows = await cursor.fetchall()
                atom_ids = [int(row[0]) for row in rows]
                if not atom_ids:
                    break
                await db.execute(
                    """
                    DELETE FROM memory_atoms_fts
                    WHERE atom_id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                await db.execute(
                    """
                    DELETE FROM memory_atoms
                    WHERE id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                total_deleted += len(atom_ids)
            await db.commit()
        return total_deleted

    async def batch_delete_by_parent(self, parent_memory_ids: list[int]) -> int:
        """批量删除多个父记忆对应的原子。"""
        normalized_ids = sorted({int(item) for item in parent_memory_ids})
        if not normalized_ids:
            return 0

        deleted_count = 0
        async with self._connect() as db:
            for index in range(0, len(normalized_ids), self._SQLITE_BATCH_SIZE):
                batch = normalized_ids[index : index + self._SQLITE_BATCH_SIZE]
                cursor = await db.execute(
                    """
                    SELECT id
                    FROM memory_atoms
                    WHERE parent_memory_id IN (
                        SELECT value FROM json_each(:parent_ids_json)
                    )
                    """,
                    {"parent_ids_json": json.dumps(batch)},
                )
                rows = await cursor.fetchall()
                atom_ids = [int(row[0]) for row in rows]
                if not atom_ids:
                    continue

                await db.execute(
                    """
                    DELETE FROM memory_atoms_fts
                    WHERE atom_id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                cursor = await db.execute(
                    """
                    DELETE FROM memory_atoms
                    WHERE id IN (SELECT value FROM json_each(:atom_ids_json))
                    """,
                    {"atom_ids_json": json.dumps(atom_ids)},
                )
                deleted_count += cursor.rowcount

            if deleted_count:
                await db.commit()
        return deleted_count

    async def get_stats(self) -> dict[str, int]:
        """返回按状态统计的原子数量。"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT status, COUNT(*) AS cnt FROM memory_atoms GROUP BY status"
            )
            rows = await cursor.fetchall()
        stats: dict[str, int] = {s.value: 0 for s in AtomStatus}
        for row in rows:
            stats[row["status"]] = int(row["cnt"])
        return stats

    async def count_atoms(self) -> int:
        """返回原子总数。"""
        async with self._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memory_atoms")
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def query_upcoming_planned(
        self,
        lookahead_sec: float = 86400.0,
        session_id: str | None = None,
        persona_id: str | None = None,
        chat_type: str | None = None,
        limit: int = 5,
    ) -> list[MemoryAtom]:
        """前瞻记忆 — 查询 event_time 在 lookahead_sec 内的 PLANNED 原子。

        Args:
            lookahead_sec: 前瞻窗口（秒），默认 86400 = 24 小时
            session_id: 限定会话
            persona_id: 限定人设
            chat_type: 当前聊天类型；群聊禁止 confidential 来源
            limit: 返回数量上限
        """
        now = time.time()
        cutoff = now + lookahead_sec
        batch_size = max(32, min(500, max(1, limit) * 2))
        atoms: list[MemoryAtom] = []
        last_event_time: float | None = None
        last_atom_id = 0
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            while len(atoms) < limit:
                cursor = await db.execute(
                    """SELECT * FROM memory_atoms
                       WHERE atom_type = :atom_type
                         AND status = 'active'
                         AND event_time IS NOT NULL
                         AND event_time >= :now
                         AND event_time <= :cutoff
                         AND (:session_id IS NULL OR session_id = :session_id)
                         AND (:persona_id IS NULL OR persona_id = :persona_id)
                         AND (
                           :last_event_time IS NULL
                           OR event_time > :last_event_time
                           OR (event_time = :last_event_time AND id > :last_atom_id)
                         )
                       ORDER BY event_time ASC, id ASC
                       LIMIT :batch_size""",
                    {
                        "atom_type": AtomType.PLANNED.value,
                        "now": now,
                        "cutoff": cutoff,
                        "session_id": session_id or None,
                        "persona_id": persona_id or None,
                        "last_event_time": last_event_time,
                        "last_atom_id": last_atom_id,
                        "batch_size": batch_size,
                    },
                )
                rows = await cursor.fetchall()
                if not rows:
                    break

                page_atoms = await filter_atoms_by_current_sources(
                    db,
                    [self._row_to_atom(row) for row in rows],
                )
                if chat_type == "group":
                    page_atoms = [
                        atom
                        for atom in page_atoms
                        if atom.parent_privacy_level in {"public", "shared"}
                    ]
                atoms.extend(page_atoms[: max(0, limit - len(atoms))])

                last_event_time = float(rows[-1]["event_time"])
                last_atom_id = int(rows[-1]["id"])
                if len(rows) < batch_size:
                    break
        return atoms[:limit]

    async def count_by_type(self) -> dict[str, int]:
        """返回前端展示用的按类型统计结果。"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT atom_type, COUNT(*) AS cnt FROM memory_atoms GROUP BY atom_type"
            )
            rows = await cursor.fetchall()
        breakdown: dict[str, int] = {}
        for row in rows:
            breakdown[row["atom_type"]] = int(row["cnt"])
        return breakdown

    def _row_to_atom(self, row: aiosqlite.Row) -> MemoryAtom:
        """将数据库行映射为 ``MemoryAtom`` 实例。"""
        metadata = self._from_json(row["metadata"])
        return MemoryAtom(
            atom_id=int(row["id"]),
            parent_memory_id=int(row["parent_memory_id"]),
            parent_revision=row["parent_revision"],
            parent_scope_key=row["parent_scope_key"],
            parent_privacy_level=row["parent_privacy_level"],
            atom_type=AtomType(row["atom_type"]),
            content=row["content"],
            entities=json.loads(row["entities"]) if row["entities"] else [],
            emotion_tags=list(metadata.get("emotion_tags", [])),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            created_at=float(row["created_at"]),
            last_accessed_at=float(row["last_accessed_at"]),
            last_reinforced_at=float(row["last_reinforced_at"])
            if row["last_reinforced_at"]
            else None,
            event_time=float(row["event_time"]) if row["event_time"] else None,
            ttl_days=float(row["ttl_days"]),
            expires_at=float(row["expires_at"]),
            status=AtomStatus(row["status"]),
            reinforcement_count=int(row["reinforcement_count"]),
            decay_type=DecayType(row["decay_type"]),
            session_id=row["session_id"],
            persona_id=row["persona_id"],
            metadata=metadata,
        )


__all__ = ["AtomStore"]
