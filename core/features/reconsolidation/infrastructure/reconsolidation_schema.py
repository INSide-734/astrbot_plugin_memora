"""再巩固候选 Store 的 SQLite schema 初始化与兼容迁移。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite


async def initialize_reconsolidation_schema(db_path: Path) -> None:
    """创建再巩固表，并为旧库补齐 apply/rollback 安全字段。"""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reconsolidation_candidates (
                candidate_id TEXT PRIMARY KEY,
                memory_id INTEGER NOT NULL,
                source_revision TEXT NOT NULL,
                old_content TEXT NOT NULL,
                old_metadata TEXT NOT NULL,
                proposed_content TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                applied_revision TEXT,
                applied_metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await _ensure_candidate_apply_columns(db)
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reconsolidation_candidates_status
            ON reconsolidation_candidates(status, updated_at, candidate_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reconsolidation_candidates_memory
            ON reconsolidation_candidates(memory_id, status)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reconsolidation_actions (
                action_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reconsolidation_actions_candidate
            ON reconsolidation_actions(candidate_id, created_at, action_id)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reconsolidation_rollback_ops (
                candidate_id TEXT PRIMARY KEY,
                expected_revision TEXT NOT NULL,
                status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reconsolidation_apply_ops (
                candidate_id TEXT PRIMARY KEY,
                expected_revision TEXT NOT NULL,
                target_metadata TEXT,
                status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await _ensure_apply_target_metadata_column(db)
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reconsolidation_rollback_ops_status
            ON reconsolidation_rollback_ops(status, updated_at, candidate_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reconsolidation_apply_ops_status
            ON reconsolidation_apply_ops(status, updated_at, candidate_id)
            """
        )
        await _deduplicate_pending_candidates(db)
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_reconsolidation_candidates_pending_key
            ON reconsolidation_candidates(
                memory_id, source_revision, proposed_content
            )
            WHERE status='pending'
            """
        )
        await db.commit()


async def _ensure_candidate_apply_columns(db: aiosqlite.Connection) -> None:
    """为旧候选表补齐 apply 后 revision 与 metadata 快照列。"""

    cursor = await db.execute("PRAGMA table_info(reconsolidation_candidates)")
    columns = {str(row[1]) for row in await cursor.fetchall()}
    await cursor.close()
    if "applied_revision" not in columns:
        await db.execute(
            "ALTER TABLE reconsolidation_candidates ADD COLUMN applied_revision TEXT"
        )
    if "applied_metadata" not in columns:
        await db.execute(
            "ALTER TABLE reconsolidation_candidates ADD COLUMN applied_metadata TEXT"
        )


async def _ensure_apply_target_metadata_column(db: aiosqlite.Connection) -> None:
    """为旧 apply intent 表补齐写前目标 metadata 快照列。"""

    cursor = await db.execute("PRAGMA table_info(reconsolidation_apply_ops)")
    columns = {str(row[1]) for row in await cursor.fetchall()}
    await cursor.close()
    if "target_metadata" not in columns:
        await db.execute(
            "ALTER TABLE reconsolidation_apply_ops ADD COLUMN target_metadata TEXT"
        )


async def _deduplicate_pending_candidates(db: aiosqlite.Connection) -> None:
    """合并旧版本重复 pending 候选，再允许创建唯一索引。"""

    await db.execute(
        """
        DELETE FROM reconsolidation_actions
        WHERE candidate_id IN (
            SELECT candidate_id
            FROM reconsolidation_candidates
            WHERE status='pending'
              AND rowid NOT IN (
                  SELECT MAX(rowid)
                  FROM reconsolidation_candidates
                  WHERE status='pending'
                  GROUP BY memory_id, source_revision, proposed_content
              )
        )
        """
    )
    await db.execute(
        """
        DELETE FROM reconsolidation_candidates
        WHERE status='pending'
          AND rowid NOT IN (
              SELECT MAX(rowid)
              FROM reconsolidation_candidates
              WHERE status='pending'
              GROUP BY memory_id, source_revision, proposed_content
          )
        """
    )


__all__ = ["initialize_reconsolidation_schema"]
