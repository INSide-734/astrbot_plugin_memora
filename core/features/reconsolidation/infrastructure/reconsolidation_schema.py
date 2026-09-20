"""再巩固候选 Store 的 SQLite schema 初始化与兼容迁移。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from astrbot.api import logger


async def initialize_reconsolidation_schema(db_path: Path) -> None:
    """创建再巩固表，并为旧库补齐 apply/rollback 安全字段。

    旧库若存在携带多条未收口 intent 的重复 pending 候选，本轮跳过去重与唯一
    索引创建（见 ``_deduplicate_pending_candidates``），由启动恢复按 canonical
    事实收口后再建索引。
    """

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
        if await _deduplicate_pending_candidates(db):
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


async def _deduplicate_pending_candidates(db: aiosqlite.Connection) -> bool:
    """合并旧版本重复 pending 候选；存在多条未收口 intent 时延迟迁移。

    重复组内**只要有多条候选携带未收口 intent**（apply/rollback 中
    ``pending``/``blocked``），就说明可能有已提交 canonical 的写尚未收口：
    重复行之间只有 ``rowid``/目标 metadata 不同，无法证明哪条才是写成功者，
    因此本轮不做任何删除、也不创建唯一索引（返回 ``False``），把全部
    candidate/op/action 原样留给 ``recover_incomplete_applies`` 按 canonical
    事实逐条收口；收口后 pending 重复自然消失，下次初始化即可正常去重建索引。

    仅当每个重复组至多一条行携带未收口 intent 时才去重：排序
    ``has_apply_intent, has_rollback_intent, rowid`` 降序，因此携带 intent 的
    行必然被保留（``begin_apply`` 在候选仍 ``pending`` 时先写 intent，
    删掉它会留下指向已不存在候选的孤儿 op 行并丢失 canonical 写收口依据）。
    """

    conflicted = await (
        await db.execute(
            """
            SELECT 1
            FROM reconsolidation_candidates c
            WHERE c.status = 'pending'
              AND (
                  EXISTS (
                      SELECT 1 FROM reconsolidation_apply_ops apply_ops
                      WHERE apply_ops.candidate_id = c.candidate_id
                        AND apply_ops.status IN ('pending', 'blocked')
                  ) OR EXISTS (
                      SELECT 1 FROM reconsolidation_rollback_ops rollback_ops
                      WHERE rollback_ops.candidate_id = c.candidate_id
                        AND rollback_ops.status IN ('pending', 'blocked')
                  )
              )
            GROUP BY c.memory_id, c.source_revision, c.proposed_content
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).fetchone()
    if conflicted is not None:
        logger.warning(
            "旧库重复 pending 候选携带多条未收口 intent，已延迟去重与唯一索引创建",
            extra={"reason_code": "legacy_pending_intent_conflict"},
        )
        return False

    await db.execute("DROP TABLE IF EXISTS _pending_dedup_losers")
    await db.execute(
        "CREATE TEMP TABLE _pending_dedup_losers (candidate_id TEXT PRIMARY KEY)"
    )
    await db.execute(
        """
        INSERT INTO _pending_dedup_losers (candidate_id)
        SELECT candidate_id
        FROM (
            SELECT candidate_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY memory_id, source_revision, proposed_content
                       ORDER BY has_apply_intent DESC,
                                has_rollback_intent DESC,
                                rowid DESC
                   ) AS rank_in_group
            FROM (
                SELECT c.rowid AS rowid,
                       c.candidate_id,
                       c.memory_id,
                       c.source_revision,
                       c.proposed_content,
                       EXISTS (
                           SELECT 1 FROM reconsolidation_apply_ops apply_ops
                           WHERE apply_ops.candidate_id = c.candidate_id
                             AND apply_ops.status IN ('pending', 'blocked')
                       ) AS has_apply_intent,
                       EXISTS (
                           SELECT 1 FROM reconsolidation_rollback_ops rollback_ops
                           WHERE rollback_ops.candidate_id = c.candidate_id
                             AND rollback_ops.status IN ('pending', 'blocked')
                       ) AS has_rollback_intent
                FROM reconsolidation_candidates c
                WHERE c.status = 'pending'
            )
        )
        WHERE rank_in_group > 1
        """
    )
    for table in (
        "reconsolidation_actions",
        "reconsolidation_apply_ops",
        "reconsolidation_rollback_ops",
    ):
        await db.execute(
            f"DELETE FROM {table} WHERE candidate_id IN "
            "(SELECT candidate_id FROM _pending_dedup_losers)"
        )
    await db.execute(
        "DELETE FROM reconsolidation_candidates WHERE candidate_id IN "
        "(SELECT candidate_id FROM _pending_dedup_losers)"
    )
    await db.execute("DROP TABLE _pending_dedup_losers")
    return True


__all__ = ["initialize_reconsolidation_schema"]
