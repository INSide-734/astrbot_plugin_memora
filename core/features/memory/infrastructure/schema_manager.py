"""数据库 Schema 检查、创建、迁移与验证。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from astrbot.api import logger

from .canonical_idempotency import (
    REQUIRED_CANONICAL_IDEMPOTENCY_TABLES,
    REQUIRED_CANONICAL_IDEMPOTENCY_TRIGGERS,
    count_current_canonical_idempotency_conflicts,
    create_canonical_idempotency_schema,
    rebuild_canonical_idempotency_mapping,
    validate_canonical_idempotency_mapping,
)

if TYPE_CHECKING:
    import aiosqlite


CURRENT_DB_VERSION = 9


@dataclass(frozen=True, slots=True)
class SchemaInspection:
    """描述一次只读 Schema 检查结果。"""

    fresh: bool
    version: int
    canonical_count: int
    document_columns: frozenset[str]
    tables: frozenset[str]
    indexes: frozenset[str]
    triggers: frozenset[str]
    idempotency_mapping_valid: bool


@dataclass(frozen=True, slots=True)
class SchemaMigrationPlan:
    """描述从现有 Schema 到当前版本的幂等迁移计划。"""

    migration_id: str
    from_version: int
    to_version: int
    canonical_count: int
    missing_columns: tuple[str, ...]
    missing_tables: tuple[str, ...]
    missing_indexes: tuple[str, ...]
    missing_triggers: tuple[str, ...]
    idempotency_rebuild_required: bool
    write_journal_required: bool


@dataclass(frozen=True, slots=True)
class SchemaValidation:
    """描述迁移后 Schema 与 canonical 数量校验结果。"""

    valid: bool
    version: int
    canonical_count: int
    reason_code: str


WriteJournalCreateCallback = Callable[[], Awaitable[None]]


class SchemaManager:
    """管理 canonical 数据库的 Schema 创建与显式版本迁移。"""

    _ALLOWED_DOCUMENT_COLUMNS = frozenset({"doc_id", "created_at", "updated_at"})
    _REQUIRED_DOCUMENT_COLUMNS = frozenset(
        {"id", "doc_id", "text", "metadata", "created_at", "updated_at"}
    )
    _DOCUMENT_COLUMN_ORDER = ("doc_id", "created_at", "updated_at")
    _DOCUMENT_COLUMN_MIGRATIONS = {
        "doc_id": 'ALTER TABLE documents ADD COLUMN "doc_id" TEXT',
        "created_at": 'ALTER TABLE documents ADD COLUMN "created_at" TEXT',
        "updated_at": 'ALTER TABLE documents ADD COLUMN "updated_at" TEXT',
    }
    _LEGACY_TRIGGER_DROP_SQL = {
        "documents_fts_insert": "DROP TRIGGER IF EXISTS documents_fts_insert",
        "documents_fts_update": "DROP TRIGGER IF EXISTS documents_fts_update",
        "documents_fts_delete": "DROP TRIGGER IF EXISTS documents_fts_delete",
    }
    _INDEX_SQL = {
        "idx_doc_metadata": (
            "CREATE INDEX IF NOT EXISTS idx_doc_metadata "
            "ON documents(json_extract(metadata, '$.session_id'))"
        ),
        "idx_doc_persona_metadata": (
            "CREATE INDEX IF NOT EXISTS idx_doc_persona_metadata "
            "ON documents(json_extract(metadata, '$.persona_id'))"
        ),
        "idx_doc_importance_metadata": (
            "CREATE INDEX IF NOT EXISTS idx_doc_importance_metadata "
            "ON documents(json_extract(metadata, '$.importance'))"
        ),
        "idx_doc_last_access_metadata": (
            "CREATE INDEX IF NOT EXISTS idx_doc_last_access_metadata "
            "ON documents(json_extract(metadata, '$.last_access_time'))"
        ),
        "idx_documents_doc_id": (
            "CREATE INDEX IF NOT EXISTS idx_documents_doc_id ON documents(doc_id)"
        ),
        "idx_hierarchy_child": (
            "CREATE INDEX IF NOT EXISTS idx_hierarchy_child ON entity_hierarchy(child)"
        ),
        "idx_hierarchy_parent": (
            "CREATE INDEX IF NOT EXISTS idx_hierarchy_parent "
            "ON entity_hierarchy(parent)"
        ),
    }
    _REQUIRED_TABLES = (
        frozenset({"documents", "entity_hierarchy", "db_version", "migration_status"})
        | REQUIRED_CANONICAL_IDEMPOTENCY_TABLES
    )
    _REQUIRED_TRIGGERS = REQUIRED_CANONICAL_IDEMPOTENCY_TRIGGERS

    def __init__(self, db_connection: aiosqlite.Connection | None = None) -> None:
        """保存由 MemoryEngine 统一管理的 SQLite 连接。"""

        self._db = db_connection

    @property
    def db_connection(self) -> aiosqlite.Connection | None:
        """返回当前 SQLite 连接，供启动期恢复流程关闭连接。"""

        return self._db

    async def close_connection(self) -> None:
        """关闭并分离当前连接，供文件级快照恢复使用。"""

        connection = self._db
        self._db = None
        if connection is not None:
            await connection.close()

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """安全引用 SQLite 标识符并转义嵌入引号。"""

        if "\x00" in identifier:
            raise ValueError("SQLite 标识符不能包含 NUL 字节")
        return f'"{identifier.replace(chr(34), chr(34) * 2)}"'

    @classmethod
    def _quote_allowed_document_column(cls, identifier: str) -> str:
        """校验并引用允许迁移的 documents 列名。"""

        if identifier not in cls._ALLOWED_DOCUMENT_COLUMNS:
            raise ValueError(f"不支持的 documents 列: {identifier!r}")
        return cls._quote_identifier(identifier)

    async def inspect_schema(self) -> SchemaInspection:
        """只读检查表、索引、版本、documents 列和 canonical 数量。"""

        if self._db is None:
            raise RuntimeError("SchemaManager 尚未绑定数据库连接")
        table_cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        tables = frozenset(str(row[0]) for row in await table_cursor.fetchall())
        index_cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
        indexes = frozenset(str(row[0]) for row in await index_cursor.fetchall())
        trigger_cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
        triggers = frozenset(str(row[0]) for row in await trigger_cursor.fetchall())
        if "documents" not in tables:
            return SchemaInspection(
                fresh=True,
                version=0,
                canonical_count=0,
                document_columns=frozenset(),
                tables=tables,
                indexes=indexes,
                triggers=triggers,
                idempotency_mapping_valid=False,
            )

        column_cursor = await self._db.execute("PRAGMA table_info(documents)")
        columns = frozenset(str(row[1]) for row in await column_cursor.fetchall())
        count_cursor = await self._db.execute("SELECT COUNT(*) FROM documents")
        count_row = await count_cursor.fetchone()
        canonical_count = int(count_row[0]) if count_row else 0
        version = 0
        if "db_version" in tables:
            version_cursor = await self._db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM db_version"
            )
            version_row = await version_cursor.fetchone()
            version = int(version_row[0]) if version_row else 0
        fresh_install = (
            canonical_count == 0
            and "db_version" not in tables
            and "entity_hierarchy" not in tables
            and "migration_status" not in tables
        )
        idempotency_structure_present = REQUIRED_CANONICAL_IDEMPOTENCY_TABLES.issubset(
            tables
        ) and REQUIRED_CANONICAL_IDEMPOTENCY_TRIGGERS.issubset(triggers)
        idempotency_mapping_valid = False
        if idempotency_structure_present:
            idempotency_mapping_valid = await validate_canonical_idempotency_mapping(
                self._db
            )
        return SchemaInspection(
            fresh=fresh_install,
            version=version,
            canonical_count=canonical_count,
            document_columns=columns,
            tables=tables,
            indexes=indexes,
            triggers=triggers,
            idempotency_mapping_valid=idempotency_mapping_valid,
        )

    @classmethod
    def build_migration_plan(
        cls,
        inspection: SchemaInspection,
        *,
        require_write_journal: bool = False,
    ) -> SchemaMigrationPlan | None:
        """根据只读检查结果构造稳定且可重试的迁移计划。"""

        if inspection.fresh:
            raise ValueError("新数据库必须走 create_fresh_schema")
        if inspection.version > CURRENT_DB_VERSION:
            raise ValueError("数据库版本高于当前插件支持版本")
        missing_columns = tuple(
            column
            for column in cls._DOCUMENT_COLUMN_ORDER
            if column not in inspection.document_columns
        )
        missing_tables = tuple(
            table
            for table in sorted(cls._REQUIRED_TABLES)
            if table not in inspection.tables
        )
        missing_indexes = tuple(
            index for index in cls._INDEX_SQL if index not in inspection.indexes
        )
        missing_triggers = tuple(
            trigger
            for trigger in sorted(cls._REQUIRED_TRIGGERS)
            if trigger not in inspection.triggers
        )
        idempotency_rebuild_required = not inspection.idempotency_mapping_valid
        journal_missing = (
            require_write_journal and "memory_write_ops" not in inspection.tables
        )
        if (
            inspection.version == CURRENT_DB_VERSION
            and not missing_columns
            and not missing_tables
            and not missing_indexes
            and not missing_triggers
            and not idempotency_rebuild_required
            and not journal_missing
        ):
            return None
        migration_id = (
            f"schema-v{inspection.version}-to-v{CURRENT_DB_VERSION}"
            if inspection.version != CURRENT_DB_VERSION
            else f"schema-v{CURRENT_DB_VERSION}-repair"
        )
        return SchemaMigrationPlan(
            migration_id=migration_id,
            from_version=inspection.version,
            to_version=CURRENT_DB_VERSION,
            canonical_count=inspection.canonical_count,
            missing_columns=missing_columns,
            missing_tables=missing_tables,
            missing_indexes=missing_indexes,
            missing_triggers=missing_triggers,
            idempotency_rebuild_required=idempotency_rebuild_required,
            write_journal_required=journal_missing,
        )

    async def create_fresh_schema(
        self,
        write_journal_create_table_cb: WriteJournalCreateCallback | None = None,
    ) -> SchemaValidation:
        """在单一事务内创建当前版本的新数据库结构。"""

        if self._db is None:
            raise RuntimeError("SchemaManager 尚未绑定数据库连接")
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            await self._drop_legacy_fts_triggers()
            await self._create_documents_table()
            inspection = await self.inspect_schema()
            for column in self._DOCUMENT_COLUMN_ORDER:
                if column in inspection.document_columns:
                    continue
                self._quote_allowed_document_column(column)
                await self._db.execute(self._DOCUMENT_COLUMN_MIGRATIONS[column])
            await self._backfill_document_columns()
            await self._create_supporting_schema(write_journal_create_table_cb)
            await rebuild_canonical_idempotency_mapping(self._db)
            await self._write_current_version("初始化当前 Schema")
            validation = await self.validate_schema(
                expected_version=CURRENT_DB_VERSION,
                expected_canonical_count=0,
                require_write_journal=write_journal_create_table_cb is not None,
            )
            if not validation.valid:
                raise RuntimeError(validation.reason_code)
            await self._db.commit()
            logger.info("已创建当前数据库 Schema: version=%s", CURRENT_DB_VERSION)
            return validation
        except BaseException:
            await self._db.rollback()
            raise

    async def migrate_existing_schema(
        self,
        plan: SchemaMigrationPlan,
        write_journal_create_table_cb: WriteJournalCreateCallback | None = None,
    ) -> SchemaValidation:
        """在显式事务内幂等执行既有数据库迁移计划。"""

        if self._db is None:
            raise RuntimeError("SchemaManager 尚未绑定数据库连接")
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            current = await self.inspect_schema()
            if current.canonical_count != plan.canonical_count:
                raise RuntimeError("schema_canonical_count_changed")
            if current.version not in {plan.from_version, plan.to_version}:
                raise RuntimeError("schema_version_changed")
            await self._drop_legacy_fts_triggers()
            await self._create_documents_table()
            current_columns = set(current.document_columns)
            for column in plan.missing_columns:
                if column in current_columns:
                    continue
                self._quote_allowed_document_column(column)
                await self._db.execute(self._DOCUMENT_COLUMN_MIGRATIONS[column])
                current_columns.add(column)
            await self._backfill_document_columns()
            await self._create_supporting_schema(write_journal_create_table_cb)
            await rebuild_canonical_idempotency_mapping(self._db)
            await self._write_current_version(
                f"Schema 迁移 {plan.from_version} -> {plan.to_version}"
            )
            validation = await self.validate_schema(
                expected_version=plan.to_version,
                expected_canonical_count=plan.canonical_count,
                require_write_journal=plan.write_journal_required,
            )
            if not validation.valid:
                raise RuntimeError(validation.reason_code)
            await self._record_completed_migration(plan)
            await self._db.commit()
            logger.info(
                "数据库 Schema 迁移完成: migration_id=%s from=%s to=%s canonical_count=%s",
                plan.migration_id,
                plan.from_version,
                plan.to_version,
                plan.canonical_count,
            )
            return validation
        except BaseException:
            await self._db.rollback()
            raise

    async def validate_schema(
        self,
        *,
        expected_version: int,
        expected_canonical_count: int,
        require_write_journal: bool = False,
    ) -> SchemaValidation:
        """验证目标版本、必要结构和 canonical 数量均满足计划。"""

        inspection = await self.inspect_schema()
        reason_code = "schema_valid"
        if inspection.fresh:
            reason_code = "schema_documents_missing"
        elif inspection.version != expected_version:
            reason_code = "schema_version_mismatch"
        elif inspection.canonical_count != expected_canonical_count:
            reason_code = "schema_canonical_count_mismatch"
        elif not self._REQUIRED_DOCUMENT_COLUMNS.issubset(inspection.document_columns):
            reason_code = "schema_columns_missing"
        elif not self._REQUIRED_TABLES.issubset(inspection.tables):
            reason_code = "schema_tables_missing"
        elif not frozenset(self._INDEX_SQL).issubset(inspection.indexes):
            reason_code = "schema_indexes_missing"
        elif not self._REQUIRED_TRIGGERS.issubset(inspection.triggers):
            reason_code = "schema_triggers_missing"
        elif not inspection.idempotency_mapping_valid:
            reason_code = "schema_idempotency_mapping_invalid"
        elif require_write_journal and "memory_write_ops" not in inspection.tables:
            reason_code = "schema_write_journal_missing"
        return SchemaValidation(
            valid=reason_code == "schema_valid",
            version=inspection.version,
            canonical_count=inspection.canonical_count,
            reason_code=reason_code,
        )

    async def create_tables(
        self,
        write_journal_create_table_cb: WriteJournalCreateCallback | None = None,
    ) -> None:
        """兼容旧调用方，自动创建或迁移；生产启动应使用迁移协调器。"""

        if self._db is None:
            return
        inspection = await self.inspect_schema()
        if inspection.fresh:
            await self.create_fresh_schema(write_journal_create_table_cb)
            return
        plan = self.build_migration_plan(
            inspection,
            require_write_journal=write_journal_create_table_cb is not None,
        )
        if plan is not None:
            await self.migrate_existing_schema(plan, write_journal_create_table_cb)

    async def _create_documents_table(self) -> None:
        """创建当前 documents 表；已存在时不改变数据。"""

        assert self._db is not None
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

    async def _backfill_document_columns(self) -> None:
        """幂等回填旧 documents 行的稳定标识和时间字段。"""

        assert self._db is not None
        await self._db.execute(
            """
            UPDATE documents SET doc_id = 'legacy-' || id
            WHERE doc_id IS NULL OR TRIM(doc_id) = ''
            """
        )
        await self._db.execute(
            """
            UPDATE documents SET created_at = datetime('now')
            WHERE created_at IS NULL OR TRIM(CAST(created_at AS TEXT)) = ''
            """
        )
        await self._db.execute(
            """
            UPDATE documents SET updated_at = COALESCE(created_at, datetime('now'))
            WHERE updated_at IS NULL OR TRIM(CAST(updated_at AS TEXT)) = ''
            """
        )

    async def _create_supporting_schema(
        self,
        write_journal_create_table_cb: WriteJournalCreateCallback | None,
    ) -> None:
        """创建索引、层级表、版本表和迁移状态表。"""

        assert self._db is not None
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_hierarchy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child TEXT NOT NULL,
                parent TEXT NOT NULL,
                UNIQUE(child, parent)
            )
            """
        )
        for index_sql in self._INDEX_SQL.values():
            await self._db.execute(index_sql)
        if write_journal_create_table_cb is not None:
            await write_journal_create_table_cb()
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS db_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                description TEXT,
                migrated_at TEXT NOT NULL,
                migration_duration_seconds REAL
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_status (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            """
        )
        await create_canonical_idempotency_schema(self._db)

    async def _write_current_version(self, description: str) -> None:
        """仅在当前版本记录不存在时追加版本行。"""

        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO db_version (
                version,
                description,
                migrated_at,
                migration_duration_seconds
            )
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM db_version WHERE version = ?
            )
            """,
            (
                CURRENT_DB_VERSION,
                description,
                datetime.now(timezone.utc).isoformat(),
                0.0,
                CURRENT_DB_VERSION,
            ),
        )

    async def _record_completed_migration(self, plan: SchemaMigrationPlan) -> None:
        """在数据库内保存不含路径和正文的迁移完成摘要。"""

        assert self._db is not None
        idempotency_conflicts = await count_current_canonical_idempotency_conflicts(
            self._db
        )
        summary = json.dumps(
            {
                "migration_id": plan.migration_id,
                "from_version": plan.from_version,
                "to_version": plan.to_version,
                "stage": "completed",
                "canonical_count": plan.canonical_count,
                "columns_added": len(plan.missing_columns),
                "triggers_added": len(plan.missing_triggers),
                "idempotency_mapping_rebuilt": plan.idempotency_rebuild_required,
                "idempotency_conflicts_preserved": idempotency_conflicts,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        await self._db.execute(
            """
            INSERT OR REPLACE INTO migration_status (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                f"schema:{plan.migration_id}",
                summary,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    async def _drop_legacy_fts_triggers(self) -> None:
        """只删除白名单中的旧 Memora FTS 触发器。"""

        if self._db is None:
            return
        cursor = await self._db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='trigger' AND tbl_name='documents'
              AND sql LIKE '%documents_fts%'
            """
        )
        rows = await cursor.fetchall()
        for row in rows:
            trigger_name = row[0]
            if not isinstance(trigger_name, str) or not trigger_name.strip():
                logger.warning("[SchemaManager] 跳过空的旧 FTS 触发器名")
                continue
            drop_sql = self._LEGACY_TRIGGER_DROP_SQL.get(trigger_name)
            if drop_sql is None:
                logger.warning("[SchemaManager] 跳过非白名单旧 FTS 触发器名")
                continue
            await self._db.execute(drop_sql)
            logger.warning("已清理旧 Memora FTS 触发器: %s", trigger_name)


__all__ = [
    "CURRENT_DB_VERSION",
    "SchemaInspection",
    "SchemaManager",
    "SchemaMigrationPlan",
    "SchemaValidation",
]
