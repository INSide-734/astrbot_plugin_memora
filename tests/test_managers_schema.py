"""SchemaManager 创建、迁移与校验测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from core.managers.schema_manager import CURRENT_DB_VERSION, SchemaManager


def _create_legacy_database(db_path: Path, *, version: int = 7) -> None:
    """创建带一条 canonical 记录的历史 Schema。"""

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
            """
        )
        connection.execute(
            "INSERT INTO documents (text, metadata) VALUES ('legacy', '{}')"
        )
        connection.execute(
            """
            CREATE TABLE db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO db_version (
                version,
                description,
                migrated_at,
                migration_duration_seconds
            ) VALUES (?, 'legacy', '2026-01-01T00:00:00+00:00', 0.0)
            """,
            (version,),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_create_tables_builds_current_schema(tmp_path: Path) -> None:
    """空数据库通过兼容入口创建当前完整 Schema。"""

    connection = await aiosqlite.connect(tmp_path / "memora.db")
    manager = SchemaManager(connection)

    await manager.create_tables()
    inspection = await manager.inspect_schema()
    await connection.close()

    assert inspection.version == CURRENT_DB_VERSION
    assert inspection.canonical_count == 0
    assert {
        "id",
        "doc_id",
        "text",
        "metadata",
        "created_at",
        "updated_at",
    }.issubset(inspection.document_columns)
    assert {
        "entity_hierarchy",
        "db_version",
        "migration_status",
        "canonical_idempotency_keys",
        "canonical_idempotency_conflicts",
    }.issubset(inspection.tables)
    assert {
        "documents_idempotency_insert",
        "documents_idempotency_update",
        "documents_idempotency_delete",
    }.issubset(inspection.triggers)
    assert inspection.idempotency_mapping_valid is True


@pytest.mark.asyncio
async def test_create_tables_skips_when_connection_missing() -> None:
    """未绑定数据库连接时兼容入口直接返回。"""

    manager = SchemaManager(None)
    await manager.create_tables()


@pytest.mark.asyncio
async def test_create_tables_migrates_missing_columns(tmp_path: Path) -> None:
    """历史 documents 表补齐列并回填稳定字段。"""

    db_path = tmp_path / "memora.db"
    _create_legacy_database(db_path)
    connection = await aiosqlite.connect(db_path)
    manager = SchemaManager(connection)

    await manager.create_tables()
    inspection = await manager.inspect_schema()
    row = await (
        await connection.execute(
            "SELECT doc_id, created_at, updated_at FROM documents WHERE id = 1"
        )
    ).fetchone()
    await connection.close()

    assert inspection.version == CURRENT_DB_VERSION
    assert {"doc_id", "created_at", "updated_at"}.issubset(inspection.document_columns)
    assert row is not None
    assert row[0] == "legacy-1"
    assert row[1]
    assert row[2]


@pytest.mark.asyncio
async def test_create_tables_writes_current_version_once(tmp_path: Path) -> None:
    """重复调用兼容入口不会重复追加当前版本行。"""

    connection = await aiosqlite.connect(tmp_path / "memora.db")
    manager = SchemaManager(connection)

    await manager.create_tables()
    await manager.create_tables()
    row = await (
        await connection.execute(
            "SELECT COUNT(*) FROM db_version WHERE version = ?",
            (CURRENT_DB_VERSION,),
        )
    ).fetchone()
    await connection.close()

    assert row is not None
    assert int(row[0]) == 1


@pytest.mark.asyncio
async def test_create_tables_invokes_write_journal_callback(tmp_path: Path) -> None:
    """传入写日志建表回调时在同一 Schema 流程内执行。"""

    connection = await aiosqlite.connect(tmp_path / "memora.db")
    manager = SchemaManager(connection)

    async def _create_write_journal() -> None:
        """创建测试用写日志表。"""

        await connection.execute(
            "CREATE TABLE IF NOT EXISTS memory_write_ops (id INTEGER PRIMARY KEY)"
        )

    await manager.create_tables(_create_write_journal)
    inspection = await manager.inspect_schema()
    await connection.close()

    assert "memory_write_ops" in inspection.tables


@pytest.mark.asyncio
async def test_drop_legacy_fts_triggers_only_removes_allowlist(tmp_path: Path) -> None:
    """旧 FTS 清理只删除固定白名单触发器。"""

    connection = await aiosqlite.connect(tmp_path / "memora.db")
    await connection.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
    await connection.execute("CREATE TABLE documents_fts (id INTEGER)")
    await connection.execute(
        """
        CREATE TRIGGER documents_fts_insert AFTER INSERT ON documents
        BEGIN
            INSERT INTO documents_fts (id) VALUES (new.id);
        END
        """
    )
    await connection.execute(
        """
        CREATE TRIGGER custom_documents_fts AFTER INSERT ON documents
        BEGIN
            INSERT INTO documents_fts (id) VALUES (new.id);
        END
        """
    )
    await connection.commit()
    manager = SchemaManager(connection)

    await manager._drop_legacy_fts_triggers()
    rows = await (
        await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
        )
    ).fetchall()
    await connection.close()

    assert [str(row[0]) for row in rows] == ["custom_documents_fts"]


@pytest.mark.asyncio
async def test_drop_legacy_fts_triggers_skips_without_connection() -> None:
    """未绑定连接时旧触发器清理直接返回。"""

    manager = SchemaManager(None)
    await manager._drop_legacy_fts_triggers()


def test_quote_allowed_document_column_rejects_unknown() -> None:
    """动态列迁移拒绝白名单外标识符。"""

    with pytest.raises(ValueError, match="不支持的 documents 列"):
        SchemaManager._quote_allowed_document_column("doc_id; DROP TABLE documents;--")


def test_quote_identifier_escapes_double_quotes() -> None:
    """SQLite 标识符中的双引号会被成对转义。"""

    quoted = SchemaManager._quote_identifier('bad"name')
    assert quoted == '"bad""name"'
