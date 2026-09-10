"""测试基础存储与连接池 — connection management, JSON helpers."""

import asyncio
import json

import pytest

from core.features.memory.infrastructure.base import BaseStore, ConnectionPool
from core.features.memory.infrastructure.base_store import (
    BaseStore as InstanceBaseStore,
)


class TestConnectionPool:
    """测试 ConnectionPool 类。"""

    @pytest.mark.asyncio
    async def test_initialize_and_acquire(self, tmp_db_path):
        """池 initializes with correct size and connections are borrowable."""
        pool = ConnectionPool(tmp_db_path, pool_size=2)
        assert pool.size == 2
        assert pool.available == 0

        await pool.initialize()
        assert pool.available == 2

        async with pool.acquire() as conn:
            assert pool.available == 1
            cursor = await conn.execute("SELECT 1")
            row = await cursor.fetchone()
            assert row[0] == 1

        assert pool.available == 2
        await pool.close()

    @pytest.mark.asyncio
    async def test_pool_exhaustion_waits(self, tmp_db_path):
        """Acquiring more than pool_size blocks until a connection is returned."""
        pool = ConnectionPool(tmp_db_path, pool_size=1)
        await pool.initialize()

        async with pool.acquire():
            # Queue is empty now; try to acquire in a task with timeout
            async def _second_acquire():
                async with pool.acquire():
                    pass

            task = asyncio.create_task(_second_acquire())
            done, _ = await asyncio.wait([task], timeout=0.3)
            assert not done, "second acquire should block while pool is exhausted"

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await pool.close()

    @pytest.mark.asyncio
    async def test_close_drains_pool(self, tmp_db_path):
        """Closing the pool releases all connections."""
        pool = ConnectionPool(tmp_db_path, pool_size=3)
        await pool.initialize()
        assert pool.available == 3
        await pool.close()
        assert pool.available == 0


class TestBaseStoreConnection:
    """测试 BaseStore 连接管理。"""

    @pytest.mark.asyncio
    async def test_one_shot_fallback(self, tmp_db_path):
        """在没有 an initialized pool, _connect falls back to a one-shot connection."""
        store = BaseStore()
        store.db_path = tmp_db_path  # type: ignore[attr-defined]

        async with store._connect() as db:
            await db.execute("CREATE TABLE IF NOT EXISTS _test (id INTEGER)")
            await db.commit()

        async with store._connect() as db:
            cursor = await db.execute("SELECT COUNT(*) FROM _test")
            row = await cursor.fetchone()
            assert row[0] == 0

    @pytest.mark.asyncio
    async def test_one_shot_connections_enable_foreign_keys(self, tmp_db_path):
        store = BaseStore()
        store.db_path = tmp_db_path  # type: ignore[attr-defined]

        async with store._connect() as db:
            cursor = await db.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_pool_based_connect(self, tmp_db_path):
        """当 a pool is initialized, _connect borrows from the pool."""
        await BaseStore.init_pool(tmp_db_path, pool_size=2)
        try:
            store = BaseStore()
            store.db_path = tmp_db_path  # type: ignore[attr-defined]

            async with store._connect() as db:
                await db.execute("CREATE TABLE IF NOT EXISTS _pool_test (id INTEGER)")
                await db.commit()

            async with store._connect() as db:
                cursor = await db.execute("SELECT COUNT(*) FROM _pool_test")
                row = await cursor.fetchone()
                assert row[0] == 0
        finally:
            await BaseStore.close_pool()

    @pytest.mark.asyncio
    async def test_pool_connections_enable_foreign_keys(self, tmp_db_path):
        await BaseStore.init_pool(tmp_db_path, pool_size=1)
        try:
            store = BaseStore()
            store.db_path = tmp_db_path  # type: ignore[attr-defined]

            async with store._connect() as db:
                cursor = await db.execute("PRAGMA foreign_keys")
                row = await cursor.fetchone()
                assert row[0] == 1
        finally:
            await BaseStore.close_pool()

    @pytest.mark.asyncio
    async def test_instance_store_connections_use_shared_pragmas(self, tmp_db_path):
        store = InstanceBaseStore(tmp_db_path)
        await store.initialize()
        try:
            cursor = await store.connection.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1
        finally:
            await store.close()

        async with store._connect() as db:
            cursor = await db.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_double_init_pool_is_idempotent(self, tmp_db_path):
        """Calling init_pool twice does not reinitialize."""
        await BaseStore.init_pool(tmp_db_path, pool_size=1)
        try:
            first_pool = BaseStore._pool
            await BaseStore.init_pool(tmp_db_path, pool_size=5)
            assert BaseStore._pool is first_pool
            assert BaseStore._pool.size == 1  # size unchanged
        finally:
            await BaseStore.close_pool()

    @pytest.mark.asyncio
    async def test_init_pool_reinitializes_for_different_db_path(self, tmp_path):
        """A second db_path must not silently reuse the previous pool."""
        first_db = str(tmp_path / "first.db")
        second_db = str(tmp_path / "second.db")

        await BaseStore.init_pool(first_db, pool_size=1)
        try:
            first_pool = BaseStore._pool
            first_store = BaseStore()
            first_store.db_path = first_db  # type: ignore[attr-defined]
            async with first_store._connect() as db:
                await db.execute("CREATE TABLE marker (name TEXT)")
                await db.execute("INSERT INTO marker VALUES ('first')")
                await db.commit()

            await BaseStore.init_pool(second_db, pool_size=1)
            assert BaseStore._pool is not first_pool
            assert BaseStore._pool is not None
            assert BaseStore._pool._db_path == second_db

            second_store = BaseStore()
            second_store.db_path = second_db  # type: ignore[attr-defined]
            async with second_store._connect() as db:
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='marker'"
                )
                assert await cursor.fetchone() is None
        finally:
            await BaseStore.close_pool()

    @pytest.mark.asyncio
    async def test_close_pool_allows_reopen_with_new_connections(self, tmp_path):
        """close_pool clears global state so a later init creates fresh connections."""
        db_path = str(tmp_path / "reopen.db")

        await BaseStore.init_pool(db_path, pool_size=1)
        first_pool = BaseStore._pool
        await BaseStore.close_pool()

        await BaseStore.init_pool(db_path, pool_size=2)
        try:
            assert BaseStore._pool is not first_pool
            assert BaseStore._pool is not None
            assert BaseStore._pool.size == 2
            assert BaseStore._pool.available == 2
        finally:
            await BaseStore.close_pool()


class TestBaseStoreJsonHelpers:
    """测试 _to_json and _from_json static methods."""

    def test_to_json_string_passthrough(self):
        """_to_json returns string inputs unchanged."""
        assert BaseStore._to_json("hello") == "hello"
        assert BaseStore._to_json('{"a":1}') == '{"a":1}'

    def test_to_json_dict(self):
        """_to_json serializes dicts to JSON."""
        result = BaseStore._to_json({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_to_json_none(self):
        """_to_json returns '{}' for None."""
        assert json.loads(BaseStore._to_json(None)) == {}

    def test_from_json_dict_passthrough(self):
        """_from_json returns dict inputs unchanged."""
        d = {"a": 1}
        assert BaseStore._from_json(d) is d

    def test_from_json_string(self):
        """_from_json parses valid JSON strings."""
        assert BaseStore._from_json('{"x": 2}') == {"x": 2}

    def test_from_json_invalid(self):
        """_from_json returns {} for invalid JSON."""
        assert BaseStore._from_json("not json") == {}
        assert BaseStore._from_json(None) == {}
        assert BaseStore._from_json("") == {}

    def test_from_json_non_dict_json(self):
        """_from_json returns {} when the JSON root is not a dict."""
        assert BaseStore._from_json("[1, 2, 3]") == {}
        assert BaseStore._from_json("42") == {}
        assert BaseStore._from_json('"string"') == {}

    def test_now_iso_is_timezone_aware(self):
        """_now_iso returns an ISO-format UTC string."""
        ts = BaseStore._now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")
