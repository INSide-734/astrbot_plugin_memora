"""会话总结任务使用的版本化 SQLite schema。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

SUMMARY_SCHEMA_VERSION = 4
_MIGRATION_ID = "summary_schema_v4"

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
    "abandoned_confirmed",
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
    """首次迁移按时间回填；已有序号只验证连续性，绝不按时间重排。"""
    cursor = await connection.execute(
        """
        SELECT id, session_id, timestamp, message_seq
        FROM messages
        ORDER BY session_id ASC, timestamp ASC, id ASC
        """
    )
    grouped: dict[str, list[tuple[int, object]]] = {}
    for row in await cursor.fetchall():
        message_id = int(row["id"] if hasattr(row, "keys") else row[0])
        session_id = str(row["session_id"] if hasattr(row, "keys") else row[1])
        raw_seq = row["message_seq"] if hasattr(row, "keys") else row[3]
        grouped.setdefault(session_id, []).append((message_id, raw_seq))

    epoch_cursors: dict[str, int] = {}
    if "cursor_seq" in await _columns(connection, "session_epochs"):
        epoch_cursor = await connection.execute(
            "SELECT session_id,cursor_seq FROM session_epochs"
        )
        epoch_cursors = {
            str(row["session_id"] if hasattr(row, "keys") else row[0]): int(
                (row["cursor_seq"] if hasattr(row, "keys") else row[1]) or 0
            )
            for row in await epoch_cursor.fetchall()
        }

    for session_id, messages in grouped.items():
        valid_rows = [
            (index, raw_seq)
            for index, (_, raw_seq) in enumerate(messages)
            if isinstance(raw_seq, int)
            and not isinstance(raw_seq, bool)
            and raw_seq >= 1
        ]
        if len(valid_rows) == len(messages):
            sequences = sorted(int(raw_seq) for _, raw_seq in valid_rows)
            if (
                any(
                    sequence != sequences[0] + index
                    for index, sequence in enumerate(sequences)
                )
                or sequences[0] > epoch_cursors.get(session_id, 0) + 1
            ):
                raise RuntimeError("message_seq_source_invalid")
            continue

        base = valid_rows[0][1] - valid_rows[0][0] if valid_rows else 1
        if (
            base < 1
            or base > epoch_cursors.get(session_id, 0) + 1
            or any(raw_seq != base + index for index, raw_seq in valid_rows)
        ):
            raise RuntimeError("message_seq_source_invalid")
        for index, (message_id, raw_seq) in enumerate(messages):
            if (
                isinstance(raw_seq, int)
                and not isinstance(raw_seq, bool)
                and raw_seq >= 1
            ):
                continue
            await connection.execute(
                "UPDATE messages SET message_seq = ? WHERE id = ? "
                "AND (message_seq IS NULL OR typeof(message_seq) <> 'integer' "
                "OR message_seq < 1)",
                (base + index, message_id),
            )


async def _validate_schema_shape(connection: Any) -> None:
    """拒绝已存在但半缺的表；不可安全猜测列语义时 fail-closed。"""
    required = {
        "sessions": {
            "session_id",
            "platform",
            "created_at",
            "last_active_at",
            "message_count",
            "participants",
            "metadata",
        },
        "messages": {
            "id",
            "session_id",
            "role",
            "content",
            "sender_id",
            "sender_name",
            "group_id",
            "platform",
            "timestamp",
            "metadata",
            "message_seq",
        },
        "session_epochs": {
            "session_id",
            "epoch",
            "cursor_seq",
            "pending_summary_json",
            "tombstoned_at",
            "updated_at",
        },
        "summary_jobs": {
            "job_id",
            "session_id",
            "session_epoch",
            "start_seq",
            "end_seq",
            "expected_count",
            "source_digest",
            "persona_id",
            "chat_type",
            "group_id",
            "scope_id",
            "gate_revision",
            "gate_snapshot_json",
            "triggered_by",
            "attempt_count",
            "next_attempt_at",
            "claim_token",
            "lease_until",
            "worker_generation",
            "failed_stage",
            "reason_code",
            "exception_type",
            "operator_action",
            "canonical_count",
            "quarantine_count",
            "discard_count",
            "mark_write_count",
            "failed_count",
            "skipped_count",
            "created_at",
            "updated_at",
        },
        "summary_job_candidates": {
            "job_id",
            "slot",
            "slot_key",
            "content_digest",
            "idempotency_key",
            "disposition",
            "status",
            "canonical_id",
            "updated_at",
        },
        "summary_task_counters": {"counter_name", "value"},
        "summary_store_meta": {"meta_key", "meta_value"},
    }
    for table, columns in required.items():
        actual = await _columns(connection, table)
        if not columns <= actual:
            raise RuntimeError("summary_schema_incomplete")


async def _validate_data_integrity(connection: Any) -> None:
    """校验来源连续性、任务约束和 epoch 区间；失败不发布半迁移库。"""
    cursor = await connection.execute(
        "SELECT session_id,message_seq FROM messages ORDER BY session_id,message_seq"
    )
    expected_session: str | None = None
    expected_seq: int | None = None
    for row in await cursor.fetchall():
        session_id = str(row["session_id"] if hasattr(row, "keys") else row[0])
        raw_seq = row["message_seq"] if hasattr(row, "keys") else row[1]
        if isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 1:
            raise RuntimeError("message_seq_source_invalid")
        if session_id != expected_session:
            expected_session = session_id
            expected_seq = raw_seq
        elif raw_seq != expected_seq:
            raise RuntimeError("message_seq_source_invalid")
        assert expected_seq is not None
        expected_seq += 1
    cursor = await connection.execute(
        """
        SELECT 1 FROM summary_jobs a JOIN summary_jobs b
          ON a.session_id=b.session_id AND a.session_epoch=b.session_epoch
         AND a.job_id < b.job_id AND a.start_seq < b.end_seq AND b.start_seq < a.end_seq
        LIMIT 1
        """
    )
    if await cursor.fetchone() is not None:
        raise RuntimeError("summary_job_overlap")
    cursor = await connection.execute(
        """
        SELECT 1 FROM summary_jobs
        WHERE end_seq <= start_seq OR expected_count != end_seq-start_seq
           OR start_seq < 0 OR source_digest IS NULL OR length(source_digest)=0
           OR status NOT IN ({statuses}) OR reason_code NOT IN ({reasons})
        LIMIT 1
        """.format(
            statuses=_quoted_values(_SUMMARY_STATUSES),
            reasons=_quoted_values(_REASON_CODES),
        )
    )
    if await cursor.fetchone() is not None:
        raise RuntimeError("summary_job_row_invalid")


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
            operator_action TEXT CHECK(
                operator_action IS NULL OR length(operator_action) BETWEEN 1 AND 64
            ),
            UNIQUE(session_id, session_epoch, start_seq, end_seq)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS summary_job_candidates (
            job_id TEXT NOT NULL,
            slot INTEGER NOT NULL CHECK(slot >= 0),
            slot_key TEXT NOT NULL CHECK(length(slot_key) BETWEEN 1 AND 128),
            content_digest TEXT NOT NULL CHECK(length(content_digest) BETWEEN 1 AND 128),
            idempotency_key TEXT NOT NULL DEFAULT '' CHECK(length(idempotency_key) <= 256),
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


async def _ensure_summary_extensions(connection: Any) -> None:
    """为已有总结表补齐操作审计和候选幂等字段。"""
    job_columns = await _columns(connection, "summary_jobs")
    if "operator_action" not in job_columns:
        await connection.execute(
            "ALTER TABLE summary_jobs ADD COLUMN operator_action TEXT CHECK("
            "operator_action IS NULL OR length(operator_action) BETWEEN 1 AND 64)"
        )
    candidate_columns = await _columns(connection, "summary_job_candidates")
    if "idempotency_key" not in candidate_columns:
        await connection.execute(
            "ALTER TABLE summary_job_candidates ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT '' CHECK(length(idempotency_key) <= 256)"
        )


async def _migrate_legacy_summary_cursors(connection: Any) -> None:
    """把旧 metadata 游标迁移到唯一的 session epoch 状态。"""
    cursor = await connection.execute(
        "SELECT session_id,metadata FROM sessions WHERE metadata IS NOT NULL"
    )
    rows = await cursor.fetchall()
    now = max(0.0, time.time())
    for row in rows:
        session_id = str(row["session_id"] if hasattr(row, "keys") else row[0])
        raw_metadata = row["metadata"] if hasattr(row, "keys") else row[1]
        try:
            metadata = json.loads(raw_metadata or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        pending = metadata.get("pending_summary")
        legacy_cursor = metadata.get("last_summarized_index", 0)
        if (
            isinstance(legacy_cursor, bool)
            or not isinstance(legacy_cursor, int)
            or legacy_cursor < 0
        ):
            continue
        max_cursor = await connection.execute(
            "SELECT COALESCE(MAX(message_seq),0) FROM messages WHERE session_id=?",
            (session_id,),
        )
        max_row = await max_cursor.fetchone()
        max_seq = int(max_row[0] or 0) if max_row else 0
        if legacy_cursor > max_seq:
            raise RuntimeError("summary_legacy_cursor_invalid")
        await connection.execute(
            """
            INSERT INTO session_epochs(
                session_id,epoch,cursor_seq,pending_summary_json,tombstoned_at,updated_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                cursor_seq=CASE
                    WHEN session_epochs.epoch=1 AND session_epochs.cursor_seq=0
                    THEN excluded.cursor_seq
                    ELSE session_epochs.cursor_seq
                END,
                updated_at=CASE
                    WHEN session_epochs.epoch=1 AND session_epochs.cursor_seq=0
                    THEN excluded.updated_at
                    ELSE session_epochs.updated_at
                END
            """,
            (session_id, 1, legacy_cursor, None, None, now),
        )
        if not isinstance(pending, dict):
            metadata.pop("last_summarized_index", None)
            await connection.execute(
                "UPDATE sessions SET metadata=? WHERE session_id=?",
                (json.dumps(metadata, ensure_ascii=False), session_id),
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

    # 生产写入会显式分配序号；直接 SQL 写入若省略该列仍由兼容触发器补齐。
    await connection.execute("DROP TRIGGER IF EXISTS messages_seq_required")
    await connection.execute("DROP TRIGGER IF EXISTS messages_seq_assign")
    await connection.execute("DROP TRIGGER IF EXISTS messages_seq_validate_insert")
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
        CREATE TRIGGER messages_seq_validate_insert
        BEFORE INSERT ON messages
        WHEN NEW.message_seq IS NOT NULL
         AND NEW.message_seq IS NOT (
             SELECT MAX(
               COALESCE((SELECT MAX(message_seq) FROM messages WHERE session_id=NEW.session_id),0),
               COALESCE((SELECT cursor_seq FROM session_epochs WHERE session_id=NEW.session_id),0)
             ) + 1
         )
        BEGIN SELECT RAISE(ABORT, 'message_seq_not_next'); END
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
                SELECT MAX(
                  COALESCE((SELECT MAX(message_seq) FROM messages WHERE session_id=NEW.session_id AND id<>NEW.id),0),
                  COALESCE((SELECT cursor_seq FROM session_epochs WHERE session_id=NEW.session_id),0)
                ) + 1
            )
            WHERE id = NEW.id;
        END
        """
    )


async def migrate_conversation_schema(connection: Any) -> None:
    """执行可回滚的版本化迁移；半缺或来源不连续时拒绝启动。"""
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
        await _ensure_summary_extensions(connection)
        await _migrate_legacy_summary_cursors(connection)
        await _validate_schema_shape(connection)
        await _validate_data_integrity(connection)
        await _ensure_indexes(connection)
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS summary_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at REAL NOT NULL CHECK(applied_at >= 0)
            )
            """
        )
        await connection.execute(
            "INSERT OR IGNORE INTO summary_schema_migrations(migration_id,applied_at) VALUES (?,?)",
            (_MIGRATION_ID, max(0.0, time.time())),
        )
        await connection.execute(f"PRAGMA user_version = {SUMMARY_SCHEMA_VERSION}")
        await connection.commit()
    except BaseException:
        await _rollback_safely(connection)
        raise


__all__ = ["SUMMARY_SCHEMA_VERSION", "migrate_conversation_schema"]
