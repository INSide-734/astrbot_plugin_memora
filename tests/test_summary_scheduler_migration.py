"""总结任务数据库迁移与旧 pending 恢复契约。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.conversation.infrastructure.summary_schema import (
    SUMMARY_SCHEMA_VERSION,
)
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.domain.summary_models import (
    SummaryReasonCode,
    SummaryWindowContext,
    WindowOutcome,
)
from core.shared.contracts.conversation import Message


def _legacy_database(path: str, metadata: dict[str, object]) -> None:
    """创建迁移前仅含 sessions/messages 的旧会话数据库。"""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                platform TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_active_at REAL NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                participants TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT,
                group_id TEXT,
                platform TEXT,
                timestamp REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        connection.execute(
            """
            INSERT INTO sessions(
                session_id,platform,created_at,last_active_at,message_count,
                participants,metadata
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                "legacy",
                "test",
                0.0,
                0.0,
                4,
                "[]",
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        for index in range(4):
            connection.execute(
                """
                INSERT INTO messages(
                    session_id,role,content,sender_id,sender_name,group_id,
                    platform,timestamp,metadata
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy",
                    "user",
                    f"消息-{index}",
                    "user-1",
                    None,
                    None,
                    "test",
                    float(index),
                    "{}",
                ),
            )


def _downgrade_reason_constraint_to_v4(path: str) -> None:
    """把临时库的 reason CHECK 改回不含 skip/invalid 的 v4 约束。"""

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='summary_jobs'"
        ).fetchone()
        assert row is not None and isinstance(row[0], str)
        current_sql = row[0]
        legacy_sql = current_sql.replace(
            ", 'no_facts', 'summary_invalid'",
            "",
        )
        assert legacy_sql != current_sql
        schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql=? WHERE type='table' AND name='summary_jobs'",
            (legacy_sql,),
        )
        connection.execute("PRAGMA writable_schema=OFF")
        connection.execute(f"PRAGMA schema_version={schema_version + 1}")
        connection.execute("PRAGMA user_version=4")


async def _context(session_id: str, epoch: int, cursor: int) -> SummaryWindowContext:
    """构造启动扫描使用的私聊固定上下文。"""
    snapshot = default_gate_snapshot()
    return SummaryWindowContext(
        session_id=session_id,
        session_epoch=epoch,
        start_seq=cursor,
        end_seq=cursor,
        chat_type="private",
        scope_id=session_id,
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=4,
        scope_key=f"private:{session_id}",
        privacy_level="shared",
        resolver_revision="resolver-test-v1",
        scope_reason_code="scope_resolved",
        scope_provenance_complete=True,
    )


@pytest.mark.asyncio
async def test_migration_preserves_legacy_summary_cursor(tmp_db_path: str) -> None:
    """迁移旧总结游标后，启动规划从旧游标继续而不是重放前缀。"""
    _legacy_database(tmp_db_path, {"last_summarized_index": 2})
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        assert await store.get_summary_epoch("legacy") == (1, 2)
        assert await store.plan_existing_frontiers(_context) == 1
        assert store.connection is not None
        cursor = await store.connection.execute(
            "SELECT start_seq,end_seq FROM summary_jobs WHERE session_id=?",
            ("legacy",),
        )
        assert [(row[0], row[1]) for row in await cursor.fetchall()] == [(2, 4)]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_migration_rejects_message_seq_that_does_not_start_at_one(
    tmp_db_path: str,
) -> None:
    """已有序号不是 1..N 连续前缀时必须拒绝启动，不能静默跳过来源。"""
    _legacy_database(tmp_db_path, {})
    with sqlite3.connect(tmp_db_path) as connection:
        connection.execute("ALTER TABLE messages ADD COLUMN message_seq INTEGER")
        connection.execute("UPDATE messages SET message_seq=id+4")
    store = ConversationStore(tmp_db_path)
    with pytest.raises(RuntimeError, match="message_seq_source_invalid"):
        await store.initialize()
    assert store.connection is None


@pytest.mark.asyncio
async def test_legacy_pending_takes_precedence_over_old_cursor(
    tmp_db_path: str,
) -> None:
    """旧 pending 的窗口起点应与迁移后的连续游标保持一致。"""
    snapshot = default_gate_snapshot()
    _legacy_database(
        tmp_db_path,
        {
            "last_summarized_index": 2,
            "pending_summary": {
                "start_index": 2,
                "end_index": 4,
                "gate_revision": snapshot.revision,
                "gate_snapshot_json": gate_snapshot_to_json(snapshot),
            },
        },
    )
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        assert await store.recover_legacy_pending() == 1
        assert store.connection is not None
        cursor = await store.connection.execute(
            "SELECT start_seq,end_seq,status FROM summary_jobs WHERE session_id=?",
            ("legacy",),
        )
        assert [(row[0], row[1], row[2]) for row in await cursor.fetchall()] == [
            (2, 4, "queued")
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_v2_schema_adds_operator_action_audit_column(tmp_db_path: str) -> None:
    """旧总结 schema 升级后应补齐操作审计和候选幂等字段。"""
    initial = ConversationStore(tmp_db_path)
    await initial.initialize()
    await initial.close()
    with sqlite3.connect(tmp_db_path) as connection:
        connection.execute(
            "ALTER TABLE summary_job_candidates DROP COLUMN idempotency_key"
        )
        connection.execute("ALTER TABLE summary_jobs DROP COLUMN operator_action")
        connection.execute("PRAGMA user_version = 2")
    migrated = ConversationStore(tmp_db_path)
    await migrated.initialize()
    try:
        assert migrated.connection is not None
        cursor = await migrated.connection.execute("PRAGMA table_info(summary_jobs)")
        assert "operator_action" in {str(row[1]) for row in await cursor.fetchall()}
        candidate_cursor = await migrated.connection.execute(
            "PRAGMA table_info(summary_job_candidates)"
        )
        assert "idempotency_key" in {
            str(row[1]) for row in await candidate_cursor.fetchall()
        }
        version_cursor = await migrated.connection.execute("PRAGMA user_version")
        version_row = await version_cursor.fetchone()
        assert version_row is not None
        assert int(version_row[0]) == SUMMARY_SCHEMA_VERSION
    finally:
        await migrated.close()


@pytest.mark.asyncio
async def test_v4_reason_constraint_migrates_for_no_facts(
    tmp_db_path: str,
) -> None:
    """v4 数据库升级后必须能持久化 completed/no-facts 结果。"""

    initial = ConversationStore(tmp_db_path)
    await initial.initialize()
    await initial.close()
    _downgrade_reason_constraint_to_v4(tmp_db_path)

    migrated = ConversationStore(tmp_db_path)
    await migrated.initialize()
    migrated.set_summary_clock(lambda: 100.0)
    try:
        for index in range(4):
            await migrated.add_message(
                Message(
                    id=0,
                    session_id="reason-v5",
                    role="user",
                    content=f"消息-{index}",
                    sender_id="user-1",
                    timestamp=float(index),
                )
            )
        context = await _context("reason-v5", 1, 0)
        assert (await migrated.plan_and_enqueue_windows(context, 4)).queued == 1
        claims = await migrated.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1

        committed = await migrated.commit_window(
            claims[0],
            WindowOutcome(
                can_advance=True,
                reason_code=SummaryReasonCode.NO_FACTS,
            ),
        )

        assert committed.status.value == "completed"
        assert committed.reason_code is SummaryReasonCode.NO_FACTS
        assert migrated.connection is not None
        version = await (
            await migrated.connection.execute("PRAGMA user_version")
        ).fetchone()
        assert version is not None
        assert int(version[0]) == SUMMARY_SCHEMA_VERSION
        index_cursor = await migrated.connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND name IN (?,?,?,?)",
            (
                "idx_summary_jobs_ready",
                "idx_summary_jobs_session_range",
                "idx_summary_jobs_lease",
                "idx_summary_candidates_status",
            ),
        )
        assert {str(row[0]) for row in await index_cursor.fetchall()} == {
            "idx_summary_jobs_ready",
            "idx_summary_jobs_session_range",
            "idx_summary_jobs_lease",
            "idx_summary_candidates_status",
        }
    finally:
        await migrated.close()
