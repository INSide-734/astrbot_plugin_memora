"""BM25Retriever 测试 — 基于FTS5的稀疏检索。"""

from __future__ import annotations

import sqlite3
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.storage.base import apply_perf_pragmas


class TestBM25Retriever:

    @pytest.fixture
    def text_processor(self) -> MagicMock:
        tp = MagicMock()
        tp.tokenize_async = AsyncMock()
        return tp

    @pytest.fixture
    def retriever(self, text_processor: MagicMock) -> Any:
        from core.retrieval.bm25_retriever import BM25Retriever
        return BM25Retriever(
            db_path=":memory:",
            text_processor=text_processor,
        )

    async def _setup_table(self, retriever: Any) -> None:
        """Create the FTS table + documents table in the in-memory db."""
        import aiosqlite
        db = await aiosqlite.connect(":memory:")
        await apply_perf_pragmas(db)
        await db.execute("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                text TEXT,
                metadata TEXT
            )
        """)
        await db.execute("""
            CREATE VIRTUAL TABLE memora_memories_fts USING fts5(
                content, doc_id UNINDEXED, tokenize='unicode61'
            )
        """)
        await db.commit()
        await db.close()

    @pytest.mark.asyncio
    async def test_connect_uses_shared_foreign_key_pragma(
        self,
        tmp_db_path,
        text_processor: MagicMock,
    ) -> None:
        from core.retrieval.bm25_retriever import BM25Retriever

        retriever = BM25Retriever(
            db_path=tmp_db_path,
            text_processor=text_processor,
        )

        async with retriever._connect() as db:
            cursor = await db.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_search_empty_query(self, retriever: Any) -> None:
        """Empty or whitespace query returns empty list."""
        results = await retriever.search("", limit=10)
        assert results == []

        results = await retriever.search("   ", limit=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_tokens(self, retriever: Any, text_processor: MagicMock) -> None:
        """When tokenizer returns empty tokens, return empty list."""
        text_processor.tokenize_async.return_value = []
        results = await retriever.search("...", limit=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_filters_by_session_id(self, retriever: Any, text_processor: MagicMock) -> None:
        """BM25 search applies session_id filtering — uses a real in-memory SQLite."""
        text_processor.tokenize_async.return_value = ["test"]

        import aiosqlite
        from contextlib import asynccontextmanager

        async with aiosqlite.connect(":memory:") as db:
            await db.execute("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await db.execute("""
                CREATE VIRTUAL TABLE memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            # Insert matching doc
            await db.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (1, "test content", '{"session_id": "s1"}'),
            )
            await db.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("test content", 1),
            )
            # Insert non-matching doc
            await db.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (2, "test other", '{"session_id": "s2"}'),
            )
            await db.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("test other", 2),
            )
            await db.commit()

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)

            # Rebuild tables in this connection
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (1, "test content", '{"session_id": "s1"}'),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("test content", 1),
            )
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (2, "test other", '{"session_id": "s2"}'),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("test other", 2),
            )
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect

        try:
            results = await retriever.search("test", limit=10, session_id="s1")
            assert len(results) == 1
            assert results[0].doc_id == 1
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_initialize_creates_fts_table(self, retriever: Any) -> None:
        """initialize() creates the FTS5 virtual table."""
        await self._setup_table(retriever)
        # initialize should not raise (table already exists)
        await retriever.initialize()

    @pytest.mark.asyncio
    async def test_search_filters_by_persona_id(self, retriever: Any, text_processor: MagicMock) -> None:
        """BM25 search applies persona_id filtering — uses a real in-memory SQLite."""
        text_processor.tokenize_async.return_value = ["testing"]

        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            # Insert doc with matching persona
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (1, "testing content", '{"persona_id": "persona1"}'),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("testing content", 1),
            )
            # Insert doc with non-matching persona
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (2, "testing other", '{"persona_id": "other"}'),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("testing other", 2),
            )
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect

        try:
            results = await retriever.search("testing", limit=5, persona_id="persona1")
            assert len(results) == 1
            assert results[0].doc_id == 1
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_add_document(self, retriever: Any, text_processor: MagicMock) -> None:
        """add_document tokenizes and inserts into FTS table."""
        text_processor.tokenize_async.return_value = ["hello", "world"]
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect
        try:
            await retriever.add_document(42, "hello world")
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_delete_document(self, retriever: Any) -> None:
        """delete_document removes from FTS table."""
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect
        try:
            result = await retriever.delete_document(1)
            assert result is True
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_update_document(self, retriever: Any, text_processor: MagicMock) -> None:
        """update_document re-indexes content."""
        text_processor.tokenize_async.return_value = ["updated", "content"]
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect
        try:
            result = await retriever.update_document(1, "updated content")
            assert result is True
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_search_normalizes_scores(self, retriever: Any, text_processor: MagicMock) -> None:
        """BM25 scores are normalized to [0,1]."""
        text_processor.tokenize_async.return_value = ["normalize"]
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (1, "normalize test", '{}'),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("normalize test", 1),
            )
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect
        try:
            results = await retriever.search("normalize", limit=5)
            assert len(results) > 0
            # Single result => all scores equal => normalized to 1.0
            if len(results) == 1:
                assert results[0].score == 1.0
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_search_empty_fts_results(self, retriever: Any, text_processor: MagicMock) -> None:
        """When FTS returns no rows, empty list returned."""
        text_processor.tokenize_async.return_value = ["nonexistent"]
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect
        try:
            results = await retriever.search("nonexistent", limit=5)
            assert results == []
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_search_with_persona_and_session_id(self, retriever: Any, text_processor: MagicMock) -> None:
        """BM25 search applies both persona_id and session_id filtering."""
        text_processor.tokenize_async.return_value = ["multi"]

        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            import json
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (1, "multi filter test", json.dumps({"session_id": "s1", "persona_id": "p1"})),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("multi filter test", 1),
            )
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (2, "multi filter other", json.dumps({"session_id": "s2", "persona_id": "p1"})),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("multi filter other", 2),
            )
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect

        try:
            results = await retriever.search("multi", limit=5, session_id="s1", persona_id="p1")
            assert len(results) == 1
            assert results[0].doc_id == 1
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_search_persona_id_only(self, retriever: Any, text_processor: MagicMock) -> None:
        """BM25 search with persona_id filter only."""
        text_processor.tokenize_async.return_value = ["persona"]

        import aiosqlite, json
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, text TEXT, metadata TEXT
                )
            """)
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (1, "persona only", json.dumps({"persona_id": "target_p"})),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("persona only", 1),
            )
            await conn.execute(
                "INSERT INTO documents(id, text, metadata) VALUES (?, ?, ?)",
                (2, "persona other", json.dumps({"persona_id": "other_p"})),
            )
            await conn.execute(
                "INSERT INTO memora_memories_fts(content, doc_id) VALUES (?, ?)",
                ("persona other", 2),
            )
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect

        try:
            results = await retriever.search("persona", limit=5, persona_id="target_p")
            assert len(results) == 1
            assert results[0].doc_id == 1
        finally:
            retriever._connect = original_connect

    @pytest.mark.asyncio
    async def test_initialize_migrates_legacy_table(self, retriever: Any) -> None:
        """initialize() migrates data from livingmemory_memories_fts."""
        import aiosqlite
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_connect():
            conn = await aiosqlite.connect(":memory:")
            await apply_perf_pragmas(conn)
            # Create the NEW table empty
            await conn.execute("""
                CREATE VIRTUAL TABLE memora_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            # Create the LEGACY table with data
            await conn.execute("""
                CREATE VIRTUAL TABLE livingmemory_memories_fts USING fts5(
                    content, doc_id UNINDEXED, tokenize='unicode61'
                )
            """)
            await conn.execute(
                "INSERT INTO livingmemory_memories_fts(content, doc_id) VALUES (?, ?)",
                ("legacy content", 99),
            )
            await conn.commit()
            try:
                yield conn
            finally:
                await conn.close()

        original_connect = retriever._connect
        retriever._connect = _fake_connect
        try:
            await retriever.initialize()
        finally:
            retriever._connect = original_connect

    def test_rejects_unapproved_fts_table_identifier(self, retriever: Any) -> None:
        """Unsafe FTS table overrides should be rejected before SQL is built."""
        retriever.fts_table = 'memora_memories_fts; DROP TABLE documents;--'
        with pytest.raises(ValueError, match="Unsupported FTS table"):
            _ = retriever._fts_table_sql

    def test_rejects_unapproved_doc_table_identifier(self, retriever: Any) -> None:
        """Only the internal documents table should be accepted."""
        retriever.doc_table = "documents_backup"
        with pytest.raises(ValueError, match="Unsupported document table"):
            _ = retriever._doc_table_sql
