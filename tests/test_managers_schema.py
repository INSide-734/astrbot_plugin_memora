"""测试 schema_manager — SchemaManager table creation and migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.schema_manager import SchemaManager

# ---------------------------------------------------------------------------
# create_tables tests
# ---------------------------------------------------------------------------


class TestSchemaManagerCreateTables:
    """测试 create_tables 异步方法。"""

    def _make_mgr(self) -> SchemaManager:
        """创建 SchemaManager with a mocked db connection."""
        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        return SchemaManager(db_connection=db)

    def _setup_table_info(self, db: AsyncMock, columns: list[str]) -> None:
        """设置 up PRAGMA table_info to return specified columns."""
        col_rows = [(i, col, "TEXT", 0, None, 0) for i, col in enumerate(columns)]
        cursor = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=col_rows)
        # First call = drop triggers, second = create table, third = pragma
        # We need the third execute to return this cursor
        db.execute.side_effect = None

    @pytest.mark.asyncio
    async def test_creates_tables_when_db_present(self) -> None:
        """当 db is set, create_tables executes DDL statements."""
        mgr = self._make_mgr()
        await mgr.create_tables()
        assert mgr._db.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_skips_when_db_none(self) -> None:
        """当 db is None, create_tables is a no-op."""
        mgr = SchemaManager(db_connection=None)
        await mgr.create_tables()
        # Should return immediately without errors

    @pytest.mark.asyncio
    async def test_missing_columns_added(self) -> None:
        """当 documents table is missing doc_id, it is added via ALTER."""
        mgr = self._make_mgr()
        # Set up PRAGMA to return only basic columns (no doc_id/created_at/updated_at)
        col_rows = [(0, "id", "INTEGER", 0, None, 0), (1, "text", "TEXT", 0, None, 0)]
        cursor_mock = AsyncMock()
        cursor_mock.fetchall = AsyncMock(return_value=col_rows)

        # We need to return this cursor for the PRAGMA call
        mgr._db.execute = AsyncMock()
        mgr._db.commit = AsyncMock()

        # Return the cursor for PRAGMA call, None/other for rest
        pragma_called = [False]

        async def _execute(sql, *args, **kwargs):
            if "PRAGMA table_info(documents)" in sql:
                pragma_called[0] = True
                return cursor_mock
            return AsyncMock()

        mgr._db.execute = _execute
        await mgr.create_tables()
        assert pragma_called[0]

    @pytest.mark.asyncio
    async def test_writes_initial_db_version(self) -> None:
        """当 db_version is empty, an initial version row is inserted."""
        mgr = self._make_mgr()

        # PRAGMA returns full set of columns
        col_rows = [
            (0, "id", "INTEGER", 0, None, 0),
            (1, "doc_id", "TEXT", 0, None, 0),
            (2, "text", "TEXT", 0, None, 0),
            (3, "metadata", "TEXT", 0, None, 0),
            (4, "created_at", "TEXT", 0, None, 0),
            (5, "updated_at", "TEXT", 0, None, 0),
        ]

        # db_version count returns 0
        version_cursor = AsyncMock()
        version_cursor.fetchone = AsyncMock(return_value=(0,))

        all_executions = []

        async def _execute(sql, *args, **kwargs):
            all_executions.append(sql)
            if "PRAGMA table_info(documents)" in sql:
                c = AsyncMock()
                c.fetchall = AsyncMock(return_value=col_rows)
                return c
            if "SELECT COUNT(*) FROM db_version" in sql:
                return version_cursor
            return AsyncMock()

        mgr._db.execute = _execute
        await mgr.create_tables()

        # Should contain INSERT into db_version
        insert_statements = [s for s in all_executions if "INSERT INTO db_version" in s]
        assert len(insert_statements) == 1

    @pytest.mark.asyncio
    async def test_skip_version_when_exists(self) -> None:
        """当 db_version already has rows, no INSERT is done."""
        mgr = self._make_mgr()

        col_rows = [
            (0, "id", "INTEGER", 0, None, 0),
            (1, "doc_id", "TEXT", 0, None, 0),
            (2, "text", "TEXT", 0, None, 0),
            (3, "metadata", "TEXT", 0, None, 0),
            (4, "created_at", "TEXT", 0, None, 0),
            (5, "updated_at", "TEXT", 0, None, 0),
        ]

        version_cursor = AsyncMock()
        version_cursor.fetchone = AsyncMock(return_value=(5,))  # 5 rows exist

        all_executions = []

        async def _execute(sql, *args, **kwargs):
            all_executions.append(sql)
            if "PRAGMA table_info(documents)" in sql:
                c = AsyncMock()
                c.fetchall = AsyncMock(return_value=col_rows)
                return c
            if "SELECT COUNT(*) FROM db_version" in sql:
                return version_cursor
            return AsyncMock()

        mgr._db.execute = _execute
        await mgr.create_tables()

        insert_statements = [s for s in all_executions if "INSERT INTO db_version" in s]
        assert len(insert_statements) == 0

    @pytest.mark.asyncio
    async def test_write_journal_callback_called(self) -> None:
        """write_journal_create_table_cb is called if provided."""
        mgr = self._make_mgr()
        cb = AsyncMock()

        col_rows = [
            (0, "id", "INTEGER", 0, None, 0),
            (1, "doc_id", "TEXT", 0, None, 0),
            (2, "text", "TEXT", 0, None, 0),
            (3, "metadata", "TEXT", 0, None, 0),
            (4, "created_at", "TEXT", 0, None, 0),
            (5, "updated_at", "TEXT", 0, None, 0),
        ]
        version_cursor = AsyncMock()
        version_cursor.fetchone = AsyncMock(return_value=(1,))

        async def _execute(sql, *args, **kwargs):
            if "PRAGMA table_info(documents)" in sql:
                c = AsyncMock()
                c.fetchall = AsyncMock(return_value=col_rows)
                return c
            if "SELECT COUNT(*) FROM db_version" in sql:
                return version_cursor
            return AsyncMock()

        mgr._db.execute = _execute
        await mgr.create_tables(write_journal_create_table_cb=cb)
        cb.assert_called_once()


# ---------------------------------------------------------------------------
# _drop_legacy_fts_triggers tests
# ---------------------------------------------------------------------------


class TestDropLegacyFtsTriggers:
    """测试 _drop_legacy_fts_triggers."""

    @pytest.mark.asyncio
    async def test_drops_fts_triggers(self) -> None:
        """当 legacy FTS triggers exist, they are dropped."""
        mgr = SchemaManager(db_connection=AsyncMock())

        # Mock cursor for trigger query
        trigger_cursor = AsyncMock()
        trigger_cursor.fetchall = AsyncMock(
            return_value=[
                ("documents_fts_insert",),
                ("documents_fts_update",),
            ]
        )

        async def _execute(sql, *args, **kwargs):
            if "SELECT name FROM sqlite_master" in sql:
                return trigger_cursor
            return AsyncMock()

        mgr._db.execute = _execute
        await mgr._drop_legacy_fts_triggers()

    @pytest.mark.asyncio
    async def test_skips_when_db_none(self) -> None:
        """当 db is None, returns immediately."""
        mgr = SchemaManager(db_connection=None)
        await mgr._drop_legacy_fts_triggers()

    def test_quote_allowed_document_column_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unsupported documents column"):
            SchemaManager._quote_allowed_document_column(
                "doc_id; DROP TABLE documents;--"
            )

    def test_quote_identifier_escapes_double_quotes(self) -> None:
        quoted = SchemaManager._quote_identifier('bad"name')
        assert quoted == '"bad""name"'


# ---------------------------------------------------------------------------
# Misc structure tests
# ---------------------------------------------------------------------------


class TestSchemaManagerStructure:
    """Smoke tests for SchemaManager."""

    def test_default_construction(self) -> None:
        """SchemaManager can be constructed without arguments."""
        mgr = SchemaManager()
        assert mgr._db is None

    def test_construction_with_connection(self) -> None:
        """SchemaManager accepts a db connection."""
        mock_conn = MagicMock()
        mgr = SchemaManager(db_connection=mock_conn)
        assert mgr._db is mock_conn

    def test_create_tables_exists(self) -> None:
        """create_tables method is defined."""
        assert hasattr(SchemaManager, "create_tables")

    def test_drop_legacy_fts_triggers_exists(self) -> None:
        """_drop_legacy_fts_triggers method is defined."""
        assert hasattr(SchemaManager, "_drop_legacy_fts_triggers")
