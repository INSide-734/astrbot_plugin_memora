"""Topic catalog 的 canonical SQLite schema 与结构迁移。"""

from __future__ import annotations

from time import time
from typing import Any

TOPIC_CATALOG_SCHEMA_VERSION = 6
TOPIC_CATALOG_TABLES = frozenset(
    {
        "memory_topic_sources",
        "scope_topics",
        "topic_catalog_state",
        "topic_catalog_dirty",
        "topic_candidate_scope_metrics",
        "topic_candidate_metric_windows",
        "topic_catalog_schema_meta",
    }
)
TOPIC_CATALOG_INDEXES = {
    "idx_topic_sources_scope": (
        "CREATE INDEX IF NOT EXISTS idx_topic_sources_scope "
        "ON memory_topic_sources(scope_key, privacy_level, generation, topic_key)"
    ),
    "idx_topic_sources_memory": (
        "CREATE INDEX IF NOT EXISTS idx_topic_sources_memory "
        "ON memory_topic_sources(memory_id, generation)"
    ),
    "idx_scope_topics_rank": (
        "CREATE INDEX IF NOT EXISTS idx_scope_topics_rank "
        "ON scope_topics(scope_key, privacy_level, generation, "
        "active_source_count DESC, last_seen_at DESC, topic_key)"
    ),
    "idx_topic_dirty_state": (
        "CREATE INDEX IF NOT EXISTS idx_topic_dirty_state "
        "ON topic_catalog_dirty(state, sequence, updated_at)"
    ),
    "idx_topic_metrics_date": (
        "CREATE INDEX IF NOT EXISTS idx_topic_metrics_date "
        "ON topic_candidate_scope_metrics(bucket_date, mode, topic_count_bucket)"
    ),
    "idx_topic_metric_windows_updated": (
        "CREATE INDEX IF NOT EXISTS idx_topic_metric_windows_updated "
        "ON topic_candidate_metric_windows(updated_at)"
    ),
}
TOPIC_CATALOG_TRIGGERS = frozenset(
    {
        "topic_catalog_documents_insert",
        "topic_catalog_documents_update",
        "topic_catalog_documents_delete",
    }
)

_TOPIC_CATALOG_COLUMN_MIGRATIONS = {
    ("topic_catalog_state", "staging_start_watermark"): (
        "INTEGER NOT NULL DEFAULT 0 CHECK(staging_start_watermark >= 0)"
    ),
    ("topic_catalog_state", "backfill_total"): (
        "INTEGER NOT NULL DEFAULT 0 CHECK(backfill_total >= 0)"
    ),
    ("topic_candidate_scope_metrics", "quality_sample_count"): (
        "INTEGER NOT NULL DEFAULT 0 CHECK(quality_sample_count >= 0)"
    ),
    ("topic_candidate_scope_metrics", "token_sample_count"): (
        "INTEGER NOT NULL DEFAULT 0 CHECK(token_sample_count >= 0)"
    ),
    ("topic_candidate_scope_metrics", "latency_sample_count"): (
        "INTEGER NOT NULL DEFAULT 0 CHECK(latency_sample_count >= 0)"
    ),
    ("topic_candidate_metric_windows", "mode"): (
        "TEXT NOT NULL DEFAULT 'off' CHECK(mode IN ('off','observe','full','top_k'))"
    ),
    ("topic_candidate_metric_windows", "topic_count_bucket"): (
        "TEXT NOT NULL DEFAULT 'unknown' CHECK(length(topic_count_bucket) BETWEEN 1 AND 32)"
    ),
    ("topic_candidate_metric_windows", "candidate_count"): (
        "INTEGER CHECK(candidate_count IS NULL OR candidate_count >= 0)"
    ),
    ("topic_candidate_metric_windows", "selector_duration_ms"): (
        "REAL CHECK(selector_duration_ms IS NULL OR selector_duration_ms >= 0)"
    ),
    ("topic_candidate_metric_windows", "prompt_chars"): (
        "INTEGER CHECK(prompt_chars IS NULL OR prompt_chars >= 0)"
    ),
    ("topic_candidate_metric_windows", "prompt_tokens"): (
        "INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0)"
    ),
}
_TOPIC_CATALOG_REQUIRED_COLUMNS = {
    "memory_topic_sources": frozenset(
        {
            "generation",
            "memory_id",
            "source_revision",
            "scope_key",
            "chat_type",
            "privacy_level",
            "topic_key",
            "display_topic",
            "observed_at",
        }
    ),
    "scope_topics": frozenset(
        {
            "generation",
            "scope_key",
            "chat_type",
            "privacy_level",
            "topic_key",
            "display_topic",
            "active_source_count",
            "first_seen_at",
            "last_seen_at",
        }
    ),
    "topic_catalog_state": frozenset(
        {
            "id",
            "active_generation",
            "staging_generation",
            "status",
            "backfill_cursor",
            "backfill_total",
            "staging_start_watermark",
            "canonical_snapshot_revision",
            "canonical_write_watermark",
            "published_dirty_watermark",
            "next_dirty_sequence",
            "rebuild_owner_token",
            "rebuild_lease_until",
            "updated_at",
            "reason_code",
        }
    ),
    "topic_catalog_dirty": frozenset(
        {
            "dirty_id",
            "memory_id",
            "operation",
            "sequence",
            "state",
            "attempt_count",
            "lease_owner_token",
            "lease_until",
            "last_error_code",
            "created_at",
            "updated_at",
        }
    ),
    "topic_candidate_scope_metrics": frozenset(
        {
            "scope_key_hash",
            "hash_key_version",
            "bucket_date",
            "mode",
            "topic_count_bucket",
            "window_count",
            "quality_sample_count",
            "token_sample_count",
            "latency_sample_count",
            "candidate_count_sum",
            "bm25_hit_count",
            "recent_fill_count",
            "identity_drop_count",
            "budget_exceeded_count",
            "catalog_degraded_count",
            "exact_reuse_count",
            "exact_topic_count",
            "duplicate_topic_count",
            "window_topic_count",
            "selector_duration_ms",
            "prompt_chars",
            "prompt_tokens",
            "terminal_state",
            "token_source_available",
            "metrics_revision",
        }
    ),
    "topic_candidate_metric_windows": frozenset(
        {
            "window_key_hash",
            "hash_key_version",
            "terminal_state",
            "token_source_available",
            "mode",
            "topic_count_bucket",
            "candidate_count",
            "selector_duration_ms",
            "prompt_chars",
            "prompt_tokens",
            "metrics_revision",
            "updated_at",
        }
    ),
    "topic_catalog_schema_meta": frozenset({"id", "schema_version", "applied_at"}),
}


async def _columns(connection: Any, table: str) -> set[str]:
    """读取固定 catalog 表的列名。"""

    cursor = await connection.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in await cursor.fetchall()}


async def topic_catalog_schema_is_valid(connection: Any) -> bool:
    """验证 catalog 表、关键列、meta 版本和单例 state 行。"""

    tables_cursor = await connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    tables = {str(row[0]) for row in await tables_cursor.fetchall()}
    if not TOPIC_CATALOG_TABLES <= tables:
        return False
    for table, required_columns in _TOPIC_CATALOG_REQUIRED_COLUMNS.items():
        if not required_columns <= await _columns(connection, table):
            return False
    meta = await (
        await connection.execute(
            "SELECT schema_version FROM topic_catalog_schema_meta WHERE id = 1"
        )
    ).fetchone()
    state = await (
        await connection.execute("SELECT 1 FROM topic_catalog_state WHERE id = 1")
    ).fetchone()
    return (
        meta is not None
        and int(meta[0]) == TOPIC_CATALOG_SCHEMA_VERSION
        and state is not None
    )


async def create_topic_catalog_schema(connection: Any) -> None:
    """创建或升级 topic catalog，并重建 canonical dirty 触发器。

    所有语句参与调用方事务；本函数不提交事务。触发器只保存 memory ID、
    操作类型和单调序号，不复制正文、旧 metadata、scope 或 topic 快照。
    """
    # v4 升级：旧版 topic_candidate_metric_windows 是明文 scope_key 裸表
    # （与 record_metric_window 写入列完全不匹配）；指标是派生数据且旧表
    # 在生产必然写入失败，无数据可保，检测到旧列结构时直接丢弃重建。
    existing_window_columns = await _columns(
        connection, "topic_candidate_metric_windows"
    )
    if existing_window_columns and "window_key_hash" not in existing_window_columns:
        await connection.execute("DROP TABLE topic_candidate_metric_windows")

    statements = (
        """
        CREATE TABLE IF NOT EXISTS memory_topic_sources (
            generation INTEGER NOT NULL CHECK(generation > 0),
            memory_id INTEGER NOT NULL CHECK(memory_id > 0),
            source_revision TEXT NOT NULL CHECK(length(source_revision) BETWEEN 1 AND 256),
            scope_key TEXT NOT NULL CHECK(length(scope_key) BETWEEN 1 AND 256),
            chat_type TEXT NOT NULL CHECK(chat_type IN ('private','group')),
            privacy_level TEXT NOT NULL
                CHECK(privacy_level IN ('public','shared','confidential')),
            topic_key TEXT NOT NULL CHECK(length(topic_key) BETWEEN 1 AND 256),
            display_topic TEXT NOT NULL CHECK(length(display_topic) BETWEEN 1 AND 256),
            observed_at REAL NOT NULL CHECK(observed_at >= 0),
            PRIMARY KEY(generation, memory_id, topic_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scope_topics (
            generation INTEGER NOT NULL CHECK(generation > 0),
            scope_key TEXT NOT NULL CHECK(length(scope_key) BETWEEN 1 AND 256),
            chat_type TEXT NOT NULL CHECK(chat_type IN ('private','group')),
            privacy_level TEXT NOT NULL
                CHECK(privacy_level IN ('public','shared','confidential')),
            topic_key TEXT NOT NULL CHECK(length(topic_key) BETWEEN 1 AND 256),
            display_topic TEXT NOT NULL CHECK(length(display_topic) BETWEEN 1 AND 256),
            active_source_count INTEGER NOT NULL CHECK(active_source_count >= 0),
            first_seen_at REAL NOT NULL CHECK(first_seen_at >= 0),
            last_seen_at REAL NOT NULL CHECK(last_seen_at >= 0),
            PRIMARY KEY(generation, scope_key, chat_type, privacy_level, topic_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS topic_catalog_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            active_generation INTEGER CHECK(active_generation IS NULL OR active_generation > 0),
            staging_generation INTEGER CHECK(staging_generation IS NULL OR staging_generation > 0),
            status TEXT NOT NULL CHECK(status IN ('empty','backfilling','ready','degraded')),
            backfill_cursor INTEGER NOT NULL DEFAULT 0 CHECK(backfill_cursor >= 0),
            backfill_total INTEGER NOT NULL DEFAULT 0 CHECK(backfill_total >= 0),
            staging_start_watermark INTEGER NOT NULL DEFAULT 0
                CHECK(staging_start_watermark >= 0),
            canonical_snapshot_revision TEXT,
            canonical_write_watermark INTEGER NOT NULL DEFAULT 0
                CHECK(canonical_write_watermark >= 0),
            published_dirty_watermark INTEGER NOT NULL DEFAULT 0
                CHECK(published_dirty_watermark >= 0),
            next_dirty_sequence INTEGER NOT NULL DEFAULT 0
                CHECK(next_dirty_sequence >= 0),
            rebuild_owner_token TEXT,
            rebuild_lease_until REAL CHECK(rebuild_lease_until IS NULL OR rebuild_lease_until >= 0),
            updated_at REAL NOT NULL DEFAULT 0 CHECK(updated_at >= 0),
            reason_code TEXT NOT NULL DEFAULT 'catalog_empty'
                CHECK(length(reason_code) BETWEEN 1 AND 128)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS topic_catalog_dirty (
            dirty_id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL CHECK(memory_id > 0) UNIQUE,
            operation TEXT NOT NULL CHECK(operation IN ('add','metadata_update','status_update','delete')),
            sequence INTEGER NOT NULL CHECK(sequence > 0),
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending','running','completed','failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            lease_owner_token TEXT,
            lease_until REAL CHECK(lease_until IS NULL OR lease_until >= 0),
            last_error_code TEXT CHECK(last_error_code IS NULL OR length(last_error_code) <= 128),
            created_at REAL NOT NULL CHECK(created_at >= 0),
            updated_at REAL NOT NULL CHECK(updated_at >= 0)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS topic_candidate_scope_metrics (
            scope_key_hash TEXT NOT NULL CHECK(length(scope_key_hash) BETWEEN 1 AND 128),
            hash_key_version INTEGER NOT NULL CHECK(hash_key_version > 0),
            bucket_date TEXT NOT NULL CHECK(length(bucket_date) = 10),
            mode TEXT NOT NULL CHECK(mode IN ('off','observe','full','top_k')),
            topic_count_bucket TEXT NOT NULL CHECK(length(topic_count_bucket) BETWEEN 1 AND 32),
            window_count INTEGER NOT NULL DEFAULT 0 CHECK(window_count >= 0),
            quality_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(quality_sample_count >= 0),
            token_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(token_sample_count >= 0),
            latency_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(latency_sample_count >= 0),
            candidate_count_sum INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count_sum >= 0),
            bm25_hit_count INTEGER NOT NULL DEFAULT 0 CHECK(bm25_hit_count >= 0),
            recent_fill_count INTEGER NOT NULL DEFAULT 0 CHECK(recent_fill_count >= 0),
            identity_drop_count INTEGER NOT NULL DEFAULT 0 CHECK(identity_drop_count >= 0),
            budget_exceeded_count INTEGER NOT NULL DEFAULT 0 CHECK(budget_exceeded_count >= 0),
            catalog_degraded_count INTEGER NOT NULL DEFAULT 0 CHECK(catalog_degraded_count >= 0),
            exact_reuse_count INTEGER NOT NULL DEFAULT 0 CHECK(exact_reuse_count >= 0),
            exact_topic_count INTEGER NOT NULL DEFAULT 0 CHECK(exact_topic_count >= 0),
            duplicate_topic_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_topic_count >= 0),
            window_topic_count INTEGER NOT NULL DEFAULT 0 CHECK(window_topic_count >= 0),
            selector_duration_ms REAL NOT NULL DEFAULT 0 CHECK(selector_duration_ms >= 0),
            prompt_chars INTEGER NOT NULL DEFAULT 0 CHECK(prompt_chars >= 0),
            prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0),
            terminal_state TEXT NOT NULL DEFAULT 'unknown'
                CHECK(terminal_state IN ('unknown','success','failed','cancelled')),
            token_source_available INTEGER NOT NULL DEFAULT 0 CHECK(token_source_available IN (0,1)),
            metrics_revision INTEGER NOT NULL DEFAULT 0 CHECK(metrics_revision >= 0),
            PRIMARY KEY(scope_key_hash, hash_key_version, bucket_date, mode, topic_count_bucket)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS topic_candidate_metric_windows (
            window_key_hash TEXT PRIMARY KEY
                CHECK(length(window_key_hash) = 64),
            hash_key_version INTEGER NOT NULL CHECK(hash_key_version > 0),
            terminal_state TEXT NOT NULL
                CHECK(terminal_state IN ('success','failed','cancelled')),
            token_source_available INTEGER NOT NULL
                CHECK(token_source_available IN (0,1)),
            mode TEXT NOT NULL DEFAULT 'off'
                CHECK(mode IN ('off','observe','full','top_k')),
            topic_count_bucket TEXT NOT NULL DEFAULT 'unknown'
                CHECK(length(topic_count_bucket) BETWEEN 1 AND 32),
            candidate_count INTEGER CHECK(candidate_count IS NULL OR candidate_count >= 0),
            selector_duration_ms REAL CHECK(selector_duration_ms IS NULL OR selector_duration_ms >= 0),
            prompt_chars INTEGER CHECK(prompt_chars IS NULL OR prompt_chars >= 0),
            prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens >= 0),
            metrics_revision INTEGER NOT NULL DEFAULT 0
                CHECK(metrics_revision >= 0),
            updated_at REAL NOT NULL CHECK(updated_at >= 0)
        ) STRICT;
        """,
        """
        CREATE TABLE IF NOT EXISTS topic_catalog_schema_meta (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            schema_version INTEGER NOT NULL CHECK(schema_version > 0),
            applied_at REAL NOT NULL CHECK(applied_at >= 0)
        )
        """,
    )
    for statement in statements:
        await connection.execute(statement)
    for (table, column), definition in _TOPIC_CATALOG_COLUMN_MIGRATIONS.items():
        if column not in await _columns(connection, table):
            await connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )
    now = max(0.0, time())
    previous_meta = await (
        await connection.execute(
            "SELECT schema_version FROM topic_catalog_schema_meta WHERE id = 1"
        )
    ).fetchone()
    previous_version = int(previous_meta[0]) if previous_meta is not None else None
    await connection.execute(
        """
        INSERT OR IGNORE INTO topic_catalog_schema_meta(id, schema_version, applied_at)
        VALUES (1, ?, ?)
        """,
        (TOPIC_CATALOG_SCHEMA_VERSION, now),
    )
    await connection.execute(
        """
        INSERT OR IGNORE INTO topic_catalog_state(
            id, status, updated_at, reason_code
        ) VALUES (1, 'empty', 0, 'catalog_empty')
        """
    )
    if previous_version is not None and previous_version < TOPIC_CATALOG_SCHEMA_VERSION:
        await connection.execute(
            """
            DELETE FROM memory_topic_sources
            WHERE generation = (
                SELECT staging_generation FROM topic_catalog_state WHERE id = 1
            )
            """
        )
        await connection.execute(
            """
            DELETE FROM scope_topics
            WHERE generation = (
                SELECT staging_generation FROM topic_catalog_state WHERE id = 1
            )
            """
        )
        await connection.execute(
            """
            UPDATE topic_catalog_state
            SET staging_generation = NULL, backfill_cursor = 0, backfill_total = 0,
                staging_start_watermark = canonical_write_watermark,
                rebuild_owner_token = NULL, rebuild_lease_until = NULL,
                status = CASE WHEN active_generation IS NULL THEN 'empty' ELSE 'ready' END,
                updated_at = ?, reason_code = 'catalog_schema_upgraded'
            WHERE id = 1
            """,
            (now,),
        )
    await connection.execute(
        """
        UPDATE topic_catalog_schema_meta
        SET schema_version = ?, applied_at = ?
        WHERE id = 1 AND schema_version < ?
        """,
        (TOPIC_CATALOG_SCHEMA_VERSION, now, TOPIC_CATALOG_SCHEMA_VERSION),
    )
    for trigger_name in TOPIC_CATALOG_TRIGGERS:
        await connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    # 触发器在 topic_catalog_state 缺行时 RAISE ABORT：这是有意的
    # fail-loudly 取舍——schema 建库保证单例行存在，缺行意味着目录
    # 状态被外部破坏，此时让 canonical 写失败比静默丢失 dirty 登记
    # 更安全（漏登记会导致发布 fence 永不满足）。
    trigger_statements = (
        """
        CREATE TRIGGER topic_catalog_documents_insert
        AFTER INSERT ON documents
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM topic_catalog_state WHERE id=1
            ) THEN RAISE(ABORT, 'topic_catalog_state_missing') END;
            UPDATE topic_catalog_state
            SET canonical_write_watermark=canonical_write_watermark + 1,
                next_dirty_sequence=next_dirty_sequence + 1,
                updated_at=CAST(strftime('%s', 'now') AS REAL)
            WHERE id=1;
            INSERT INTO topic_catalog_dirty(
                memory_id, operation, sequence, state, attempt_count,
                created_at, updated_at
            )
            SELECT NEW.id, 'add', next_dirty_sequence, 'pending', 0,
                   CAST(strftime('%s', 'now') AS REAL),
                   CAST(strftime('%s', 'now') AS REAL)
            FROM topic_catalog_state
            WHERE id=1
            ON CONFLICT(memory_id) DO UPDATE SET
                operation=excluded.operation,
                sequence=excluded.sequence,
                state=CASE WHEN topic_catalog_dirty.state='running'
                             THEN 'running' ELSE 'pending' END,
                attempt_count=CASE WHEN topic_catalog_dirty.state='running'
                                     THEN topic_catalog_dirty.attempt_count ELSE 0 END,
                lease_until=CASE WHEN topic_catalog_dirty.state='running'
                                   THEN topic_catalog_dirty.lease_until ELSE NULL END,
                lease_owner_token=CASE WHEN topic_catalog_dirty.state='running'
                                         THEN topic_catalog_dirty.lease_owner_token ELSE NULL END,
                last_error_code=NULL,
                updated_at=excluded.updated_at;
        END
        """,
        """
        CREATE TRIGGER topic_catalog_documents_update
        AFTER UPDATE OF metadata, text, updated_at, created_at ON documents
        WHEN OLD.text IS NOT NEW.text
          OR OLD.created_at IS NOT NEW.created_at
          OR NOT json_valid(OLD.metadata)
          OR NOT json_valid(NEW.metadata)
          OR(
            json_valid(OLD.metadata) AND json_valid(NEW.metadata)
            AND(
              json_extract(OLD.metadata, '$.topics') IS NOT json_extract(NEW.metadata, '$.topics')
              OR json_extract(OLD.metadata, '$.topic_observed_at') IS NOT json_extract(NEW.metadata, '$.topic_observed_at')

              OR json_extract(OLD.metadata, '$.scope_key') IS NOT json_extract(NEW.metadata, '$.scope_key')
              OR json_extract(OLD.metadata, '$.privacy_level') IS NOT json_extract(NEW.metadata, '$.privacy_level')
              OR json_extract(OLD.metadata, '$.chat_type') IS NOT json_extract(NEW.metadata, '$.chat_type')
              OR json_extract(OLD.metadata, '$.resolver_revision') IS NOT json_extract(NEW.metadata, '$.resolver_revision')
              OR json_extract(OLD.metadata, '$.source_provenance_complete') IS NOT json_extract(NEW.metadata, '$.source_provenance_complete')
              OR json_extract(OLD.metadata, '$.gate_disposition') IS NOT json_extract(NEW.metadata, '$.gate_disposition')
              OR json_extract(OLD.metadata, '$.summary_source_orphan') IS NOT json_extract(NEW.metadata, '$.summary_source_orphan')
              OR json_extract(OLD.metadata, '$.status') IS NOT json_extract(NEW.metadata, '$.status')
              OR json_extract(OLD.metadata, '$.memory_status') IS NOT json_extract(NEW.metadata, '$.memory_status')
            )
          )
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM topic_catalog_state WHERE id=1
            ) THEN RAISE(ABORT, 'topic_catalog_state_missing') END;
            UPDATE topic_catalog_state
            SET canonical_write_watermark=canonical_write_watermark + 1,
                next_dirty_sequence=next_dirty_sequence + 1,
                updated_at=CAST(strftime('%s', 'now') AS REAL)
            WHERE id=1;
            INSERT INTO topic_catalog_dirty(
                memory_id, operation, sequence, state, attempt_count,
                created_at, updated_at
            )
            SELECT NEW.id,
                   CASE
                       WHEN json_valid(OLD.metadata)
                        AND json_valid(NEW.metadata)
                        AND(
                          json_extract(OLD.metadata, '$.status')
                            IS NOT json_extract(NEW.metadata, '$.status')
                          OR json_extract(OLD.metadata, '$.memory_status')
                            IS NOT json_extract(NEW.metadata, '$.memory_status')
                        )
                       THEN 'status_update'
                       ELSE 'metadata_update'
                   END,
                   next_dirty_sequence, 'pending', 0,
                   CAST(strftime('%s', 'now') AS REAL),
                   CAST(strftime('%s', 'now') AS REAL)
            FROM topic_catalog_state
            WHERE id=1
            ON CONFLICT(memory_id) DO UPDATE SET
                operation=excluded.operation,
                sequence=excluded.sequence,
                state=CASE WHEN topic_catalog_dirty.state='running'
                             THEN 'running' ELSE 'pending' END,
                attempt_count=CASE WHEN topic_catalog_dirty.state='running'
                                     THEN topic_catalog_dirty.attempt_count ELSE 0 END,
                lease_until=CASE WHEN topic_catalog_dirty.state='running'
                                   THEN topic_catalog_dirty.lease_until ELSE NULL END,
                lease_owner_token=CASE WHEN topic_catalog_dirty.state='running'
                                         THEN topic_catalog_dirty.lease_owner_token ELSE NULL END,
                last_error_code=NULL,
                updated_at=excluded.updated_at;
        END
        """,
        """
        CREATE TRIGGER topic_catalog_documents_delete
        AFTER DELETE ON documents
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM topic_catalog_state WHERE id=1
            ) THEN RAISE(ABORT, 'topic_catalog_state_missing') END;
            UPDATE topic_catalog_state
            SET canonical_write_watermark=canonical_write_watermark + 1,
                next_dirty_sequence=next_dirty_sequence + 1,
                updated_at=CAST(strftime('%s', 'now') AS REAL)
            WHERE id=1;
            INSERT INTO topic_catalog_dirty(
                memory_id, operation, sequence, state, attempt_count,
                created_at, updated_at
            )
            SELECT OLD.id, 'delete', next_dirty_sequence, 'pending', 0,
                   CAST(strftime('%s', 'now') AS REAL),
                   CAST(strftime('%s', 'now') AS REAL)
            FROM topic_catalog_state
            WHERE id=1
            ON CONFLICT(memory_id) DO UPDATE SET
                operation=excluded.operation,
                sequence=excluded.sequence,
                state=CASE WHEN topic_catalog_dirty.state='running'
                             THEN 'running' ELSE 'pending' END,
                attempt_count=CASE WHEN topic_catalog_dirty.state='running'
                                     THEN topic_catalog_dirty.attempt_count ELSE 0 END,
                lease_until=CASE WHEN topic_catalog_dirty.state='running'
                                   THEN topic_catalog_dirty.lease_until ELSE NULL END,
                lease_owner_token=CASE WHEN topic_catalog_dirty.state='running'
                                         THEN topic_catalog_dirty.lease_owner_token ELSE NULL END,
                last_error_code=NULL,
                updated_at=excluded.updated_at;
        END
        """,
    )
    for statement in trigger_statements:
        await connection.execute(statement)
    for statement in TOPIC_CATALOG_INDEXES.values():
        await connection.execute(statement)


__all__ = [
    "TOPIC_CATALOG_INDEXES",
    "TOPIC_CATALOG_SCHEMA_VERSION",
    "TOPIC_CATALOG_TABLES",
    "TOPIC_CATALOG_TRIGGERS",
    "create_topic_catalog_schema",
    "topic_catalog_schema_is_valid",
]
