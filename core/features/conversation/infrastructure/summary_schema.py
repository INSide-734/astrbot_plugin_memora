"""会话总结任务使用的版本化 SQLite schema。"""

from __future__ import annotations

import asyncio
from typing import Any

SUMMARY_SCHEMA_VERSION = 1

_SUMMARY_STATUSES = (
    "queued",
    "running",
    "failed",
    "completed",
    "blocked",
    "unknown",
    "cancelled",
    "abandoned",
)
_CANDIDATE_STATUSES = ("planned", "writing", "committed", "failed", "unknown")
_DISPOSITIONS = (
    "quarantined",
    "discard",
    "mark_write",
    "canonical",
    "skipped_idempotent",
    "failed",
)
_REASON_CODES = (
    "accepted",
    "queued",
    "duplicate",
    "no_window",
    "blocked",
    "unknown",
    "cancelled",
    "store_unavailable",
    "migration_failed",
    "source_incomplete",
    "source_digest_mismatch",
    "claim_lost",
    "epoch_fenced",
    "generation_fenced",
    "lease_expired",
    "retry_scheduled",
    "retry_exhausted",
    "invalid_action",
    "invalid_slot",
    "ledger_unresolved",
    "trim_blocked",
    "legacy_pending",
    "legacy_pending_invalid",
    "completed",
)


def _quoted_values(values: tuple[str, ...]) -> str:
    """生成仅由本模块固定常量组成的 SQL CHECK 值列表。"""
    return ", ".join(f"'{value}'" for value in values)


async def _rollback_safely(connection: Any) -> None:
    """等待 SQLite 回滚完成后再继续传播原始迁移异常。"""
    try:
        task = asyncio.create_task(connection.rollback())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
    except BaseException:
        return


async def _columns(connection: Any, table: str) -> set[str]:
    """读取固定白名单表的列名。"""
    cursor = await connection.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _ensure_base_schema(connection: Any) -> None:
    """创建会话基础表，并为旧消息增加不可变序号列。"""
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            platform TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_active_at REAL NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            participants TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            sender_name TEXT,
            group_id TEXT,
            platform TEXT,
            timestamp REAL NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            message_seq INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
        """
    )
    if "message_seq" not in await _columns(connection, "messages"):
        await connection.execute("ALTER TABLE messages ADD COLUMN message_seq INTEGER")


async def _backfill_message_sequences(connection: Any) -> None:
    """只为缺失或非法序号的历史消息按 timestamp、id 一次性回填。"""
    cursor = await connection.execute(
        """
        SELECT id, session_id
        FROM messages
        WHERE message_seq IS NULL OR message_seq < 1
        ORDER BY session_id ASC, timestamp ASC, id ASC
        """
    )
    rows = await cursor.fetchall()
    next_seq: dict[str, int] = {}
    for row in rows:
        session_id = str(row["session_id"] if hasattr(row, "keys") else row[1])
        message_id = int(row["id"] if hasattr(row, "keys") else row[0])
        if session_id not in next_seq:
            existing = await connection.execute(
                "SELECT COALESCE(MAX(message_seq), 0) FROM messages WHERE session_id = ? AND message_seq >= 1",
                (session_id,),
            )
            value = await existing.fetchone()
            next_seq[session_id] = int(value[0] if value else 0)
        next_seq[session_id] += 1
        await connection.execute(
            "UPDATE messages SET message_seq = ? WHERE id = ? AND (message_seq IS NULL OR message_seq < 1)",
            (next_seq[session_id], message_id),
        )


async def _ensure_summary_tables(connection: Any) -> None:
    """创建总结任务、候选 ledger、epoch 和累计计数表。"""
    statuses = _quoted_values(_SUMMARY_STATUSES)
    candidate_statuses = _quoted_values(_CANDIDATE_STATUSES)
    dispositions = _quoted_values(_DISPOSITIONS)
    reasons = _quoted_values(_REASON_CODES)
    statements = (
        """
        CREATE TABLE IF NOT EXISTS session_epochs (
            session_id TEXT PRIMARY KEY,
            epoch INTEGER NOT NULL CHECK(epoch > 0),
            cursor_seq INTEGER NOT NULL DEFAULT 0 CHECK(cursor_seq >= 0),
            pending_summary_json TEXT,
            tombstoned_at REAL,
            updated_at REAL NOT NULL DEFAULT 0 CHECK(updated_at >= 0)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS summary_jobs (
            job_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            session_epoch INTEGER NOT NULL CHECK(session_epoch > 0),
            start_seq INTEGER NOT NULL CHECK(start_seq >= 0),
            end_seq INTEGER NOT NULL CHECK(end_seq > start_seq),
            expected_count INTEGER NOT NULL
                CHECK(expected_count = end_seq - start_seq AND expected_count > 0),
            source_digest TEXT NOT NULL CHECK(length(source_digest) BETWEEN 1 AND 128),
            persona_id TEXT,
            chat_type TEXT,
            group_id TEXT,
            scope_id TEXT,
            gate_revision TEXT NOT NULL DEFAULT '' CHECK(length(gate_revision) <= 256),
            gate_snapshot_json TEXT NOT NULL DEFAULT '{{}}'
                CHECK(json_valid(gate_snapshot_json)),
            triggered_by TEXT NOT NULL CHECK(length(triggered_by) BETWEEN 1 AND 64),
            status TEXT NOT NULL CHECK(status IN ({statuses})),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            next_attempt_at REAL NOT NULL DEFAULT 0 CHECK(next_attempt_at >= 0),
            claim_token TEXT,
            lease_until REAL CHECK(lease_until IS NULL OR lease_until >= 0),
            worker_generation INTEGER NOT NULL DEFAULT 0 CHECK(worker_generation >= 0),
            failed_stage TEXT,
            reason_code TEXT NOT NULL CHECK(reason_code IN ({reasons})),
            exception_type TEXT,
            canonical_count INTEGER NOT NULL DEFAULT 0 CHECK(canonical_count >= 0),
            quarantine_count INTEGER NOT NULL DEFAULT 0 CHECK(quarantine_count >= 0),
            discard_count INTEGER NOT NULL DEFAULT 0 CHECK(discard_count >= 0),
            mark_write_count INTEGER NOT NULL DEFAULT 0 CHECK(mark_write_count >= 0),
            failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
            skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
            created_at REAL NOT NULL CHECK(created_at >= 0),
            updated_at REAL NOT NULL CHECK(updated_at >= 0),
            UNIQUE(session_id, session_epoch, start_seq, end_seq)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS summary_job_candidates (
            job_id TEXT NOT NULL,
            slot INTEGER NOT NULL CHECK(slot >= 0),
            slot_key TEXT NOT NULL CHECK(length(slot_key) BETWEEN 1 AND 128),
            content_digest TEXT NOT NULL CHECK(length(content_digest) BETWEEN 1 AND 128),
            disposition TEXT CHECK(disposition IS NULL OR disposition IN ({dispositions})),
            status TEXT NOT NULL CHECK(status IN ({candidate_statuses})),
            canonical_id INTEGER CHECK(canonical_id IS NULL OR canonical_id > 0),
            updated_at REAL NOT NULL CHECK(updated_at >= 0),
            PRIMARY KEY(job_id, slot),
            UNIQUE(job_id, slot_key),
            FOREIGN KEY(job_id) REFERENCES summary_jobs(job_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS summary_task_counters (
            counter_name TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0 CHECK(value >= 0)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS summary_store_meta (
            meta_key TEXT PRIMARY KEY,
            meta_value TEXT NOT NULL
        )
        """,
    )
    for statement in statements:
        await connection.execute(statement)
    for counter in (
        "canonical_total",
        "quarantine_total",
        "discard_total",
        "mark_write_total",
        "failed_candidate_total",
        "skipped_idempotent_total",
    ):
        await connection.execute(
            "INSERT OR IGNORE INTO summary_task_counters(counter_name, value) VALUES (?, 0)",
            (counter,),
        )


async def _ensure_indexes(connection: Any) -> None:
    """创建消息序号、任务 ready/lease、候选和不可变序号约束。"""
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_session_seq ON messages(session_id, message_seq)",
        "CREATE INDEX IF NOT EXISTS idx_msg_session_seq ON messages(session_id, message_seq ASC)",
        "CREATE INDEX IF NOT EXISTS idx_summary_jobs_ready ON summary_jobs(status, next_attempt_at, session_id, start_seq, created_at, job_id)",
        "CREATE INDEX IF NOT EXISTS idx_summary_jobs_session_range ON summary_jobs(session_id, session_epoch, start_seq, end_seq)",
        "CREATE INDEX IF NOT EXISTS idx_summary_jobs_lease ON summary_jobs(status, lease_until)",
        "CREATE INDEX IF NOT EXISTS idx_summary_candidates_status ON summary_job_candidates(job_id, status)",
    )
    for statement in statements:
        await connection.execute(statement)

    # 生产写入会显式分配序号；旧的直接 SQL 写入若省略该列，则在插入后
    # 以当前会话最大值补齐，避免升级后遗留调用制造无序来源。只有 NULL
    # 旧值允许被补齐，已有合法序号仍由不可变触发器保护。
    await connection.execute("DROP TRIGGER IF EXISTS messages_seq_required")
    await connection.execute("DROP TRIGGER IF EXISTS messages_seq_assign")
    await connection.execute("DROP TRIGGER IF EXISTS messages_seq_immutable")
    await connection.execute(
        """
        CREATE TRIGGER messages_seq_immutable
        BEFORE UPDATE OF message_seq ON messages
        WHEN OLD.message_seq IS NOT NULL
             AND OLD.message_seq IS NOT NEW.message_seq
        BEGIN SELECT RAISE(ABORT, 'message_seq_immutable'); END
        """
    )
    await connection.execute(
        """
        CREATE TRIGGER messages_seq_assign
        AFTER INSERT ON messages
        WHEN NEW.message_seq IS NULL OR NEW.message_seq < 1
        BEGIN
            UPDATE messages
            SET message_seq = (
                SELECT COALESCE(MAX(message_seq), 0)
                FROM messages
                WHERE session_id = NEW.session_id AND id <> NEW.id
            ) + 1
            WHERE id = NEW.id;
        END
        """
    )


async def migrate_conversation_schema(connection: Any) -> None:
    """在一个立即事务中创建或升级 conversations.db，失败即回滚。"""
    try:
        await connection.execute("BEGIN IMMEDIATE")
        cursor = await connection.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = int(row[0] if row else 0)
        if current_version > SUMMARY_SCHEMA_VERSION:
            raise RuntimeError("conversations.db schema version is newer")
        await _ensure_base_schema(connection)
        await _backfill_message_sequences(connection)
        await _ensure_summary_tables(connection)
        await _ensure_indexes(connection)
        await connection.execute(f"PRAGMA user_version = {SUMMARY_SCHEMA_VERSION}")
        await connection.commit()
    except BaseException:
        await _rollback_safely(connection)
        raise


__all__ = ["SUMMARY_SCHEMA_VERSION", "migrate_conversation_schema"]
