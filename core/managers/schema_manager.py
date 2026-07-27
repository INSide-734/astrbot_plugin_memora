"""
数据库模式管理器
负责 documents 表和其他系统表的创建、迁移和清理
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    import aiosqlite


class SchemaManager:
    """数据库模式管理 — 建表、迁移、旧触发器清理"""

    _ALLOWED_DOCUMENT_COLUMNS = frozenset({"doc_id", "created_at", "updated_at"})

    def __init__(self, db_connection: aiosqlite.Connection | None = None) -> None:
        self._db = db_connection

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """安全地引用 SQLite 标识符，转义嵌入的引号。"""
        if "\x00" in identifier:
            raise ValueError("SQLite identifier contains NUL byte")
        return f'"{identifier.replace('"', '""')}"'

    @classmethod
    def _quote_allowed_document_column(cls, identifier: str) -> str:
        if identifier not in cls._ALLOWED_DOCUMENT_COLUMNS:
            raise ValueError(f"Unsupported documents column: {identifier!r}")
        return cls._quote_identifier(identifier)

    async def create_tables(self, write_journal_create_table_cb=None) -> None:
        """创建所有系统表"""
        if self._db is None:
            return

        await self._drop_legacy_fts_triggers()

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
        """)

        cursor = await self._db.execute("PRAGMA table_info(documents)")
        column_rows = await cursor.fetchall()
        existing_columns = {row[1] for row in column_rows}

        for col_name in ("doc_id", "created_at", "updated_at"):
            if col_name not in existing_columns:
                quoted_column = self._quote_allowed_document_column(col_name)
                await self._db.execute(
                    f"ALTER TABLE documents ADD COLUMN {quoted_column} TEXT"
                )
                logger.warning(
                    f"[SchemaManager] 检测到旧版 documents 表结构，已补齐字段: {col_name}"
                )

        await self._db.execute("""
            UPDATE documents SET doc_id = 'legacy-' || id
            WHERE doc_id IS NULL OR TRIM(doc_id) = ''
        """)
        await self._db.execute("""
            UPDATE documents SET created_at = datetime('now')
            WHERE created_at IS NULL OR TRIM(CAST(created_at AS TEXT)) = ''
        """)
        await self._db.execute("""
            UPDATE documents SET updated_at = COALESCE(created_at, datetime('now'))
            WHERE updated_at IS NULL OR TRIM(CAST(updated_at AS TEXT)) = ''
        """)

        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_doc_metadata "
            "ON documents(json_extract(metadata, '$.session_id'))",
            "CREATE INDEX IF NOT EXISTS idx_doc_persona_metadata "
            "ON documents(json_extract(metadata, '$.persona_id'))",
            "CREATE INDEX IF NOT EXISTS idx_doc_importance_metadata "
            "ON documents(json_extract(metadata, '$.importance'))",
            "CREATE INDEX IF NOT EXISTS idx_doc_last_access_metadata "
            "ON documents(json_extract(metadata, '$.last_access_time'))",
            "CREATE INDEX IF NOT EXISTS idx_documents_doc_id ON documents(doc_id)",
        ]:
            await self._db.execute(idx_sql)

        if write_journal_create_table_cb:
            await write_journal_create_table_cb()

        # G3: 实体层级表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS entity_hierarchy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child TEXT NOT NULL,
                parent TEXT NOT NULL,
                UNIQUE(child, parent)
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_hierarchy_child ON entity_hierarchy(child)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_hierarchy_parent "
            "ON entity_hierarchy(parent)"
        )

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS migration_status (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)
        await self._db.commit()

        cursor = await self._db.execute("SELECT COUNT(*) FROM db_version")
        version_result = await cursor.fetchone()
        version_count = version_result[0] if version_result else 0

        if version_count == 0:
            from datetime import datetime, timezone

            _CURRENT_DB_VERSION = 8  # noqa: N806

            await self._db.execute(
                """
                INSERT INTO db_version (version, description, migrated_at, migration_duration_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _CURRENT_DB_VERSION,
                    "初始版本 - 当前架构",
                    datetime.now(timezone.utc).isoformat(),
                    0.0,
                ),
            )
            await self._db.commit()
            logger.info(f"已初始化数据库版本信息: v{_CURRENT_DB_VERSION}")

    async def _drop_legacy_fts_triggers(self) -> None:
        if self._db is None:
            return
        cursor = await self._db.execute("""
            SELECT name FROM sqlite_master
            WHERE type='trigger' AND tbl_name='documents'
              AND sql LIKE '%documents_fts%'
        """)
        rows = await cursor.fetchall()
        for row in rows:
            trigger_name = row[0]
            if not isinstance(trigger_name, str) or not trigger_name.strip():
                logger.warning("[SchemaManager] 跳过空的旧 FTS 触发器名")
                continue
            quoted_trigger_name = self._quote_identifier(trigger_name)
            await self._db.execute(f"DROP TRIGGER IF EXISTS {quoted_trigger_name}")
            logger.warning(f"已清理旧 Memora FTS 触发器: {trigger_name}")
