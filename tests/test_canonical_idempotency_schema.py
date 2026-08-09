"""Canonical idempotency schema、迁移与跨连接竞态测试。"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from core.managers.schema_manager import CURRENT_DB_VERSION, SchemaManager
from core.storage.canonical_idempotency import (
    find_canonical_memory_id_by_idempotency_key,
)


def _metadata(key: str, *, revision: str) -> str:
    """构造包含作用域、隐私和 revision 的 canonical metadata。"""

    return json.dumps(
        {
            "idempotency_key": key,
            "privacy_level": "private",
            "revision": revision,
            "scope_key": "session:canonical-idempotency",
        },
        ensure_ascii=True,
        sort_keys=True,
    )


def _create_v8_database(db_path: Path) -> list[tuple[int, str]]:
    """创建含重复规范化 key、但不删除 canonical 的 v8 数据库。"""

    rows = [
        (1, _metadata(" retry-key ", revision="revision-owner")),
        (2, _metadata("retry-key", revision="revision-duplicate")),
        (3, _metadata("unique-key", revision="revision-unique")),
    ]
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO documents (
                id, doc_id, text, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    memory_id,
                    f"legacy-{memory_id}",
                    f"canonical-{memory_id}",
                    metadata,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                )
                for memory_id, metadata in rows
            ],
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
                version, description, migrated_at, migration_duration_seconds
            ) VALUES (8, 'legacy-v8', '2026-01-01T00:00:00+00:00', 0.0)
            """
        )
        connection.commit()
    finally:
        connection.close()
    return rows


async def _insert_or_reuse(
    connection: aiosqlite.Connection,
    *,
    doc_id: str,
    key: str,
    barrier: asyncio.Barrier,
) -> tuple[int, bool]:
    """模拟独立引擎插入；唯一冲突时读取已提交 owner。"""

    await barrier.wait()
    try:
        await connection.execute("BEGIN IMMEDIATE")
        cursor = await connection.execute(
            """
            INSERT INTO documents (
                doc_id, text, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                "same canonical candidate",
                _metadata(key, revision=doc_id),
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        memory_id = int(cursor.lastrowid)
        await cursor.close()
        await connection.commit()
        return memory_id, True
    except sqlite3.IntegrityError:
        await connection.rollback()
        existing_id = await find_canonical_memory_id_by_idempotency_key(
            connection,
            key,
        )
        assert existing_id is not None
        return existing_id, False


def _multiprocess_insert_worker(
    db_path: str,
    key: str,
    doc_id: str,
    start_event: Any,
    result_queue: Any,
) -> None:
    """在独立进程内写 canonical，并在唯一冲突后读取 owner。"""

    connection = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 10000")
        start_event.wait(timeout=10.0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO documents (
                    doc_id, text, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    "same multiprocess canonical candidate",
                    _metadata(key, revision=doc_id),
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            memory_id = int(cursor.lastrowid)
            connection.commit()
            result_queue.put((memory_id, True, None))
        except sqlite3.IntegrityError:
            connection.rollback()
            row = connection.execute(
                """
                SELECT canonical_memory_id
                FROM canonical_idempotency_keys
                WHERE idempotency_key = ?
                """,
                (key.strip(),),
            ).fetchone()
            if row is None:
                raise RuntimeError("canonical idempotency owner missing")
            result_queue.put((int(row[0]), False, None))
    except BaseException as exc:
        result_queue.put((None, False, exc.__class__.__name__))
    finally:
        connection.close()


def _run_multiprocess_race(db_path: Path) -> list[tuple[int | None, bool, str | None]]:
    """启动并完整回收两个 spawn writer，返回低敏结果。"""

    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_multiprocess_insert_worker,
            args=(
                str(db_path),
                "multiprocess-race",
                f"process-{index}",
                start_event,
                result_queue,
            ),
        )
        for index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=15.0)
        if any(process.is_alive() for process in processes):
            raise AssertionError("multiprocess idempotency worker timed out")
        if any(process.exitcode != 0 for process in processes):
            raise AssertionError("multiprocess idempotency worker failed")
        return [result_queue.get(timeout=5.0) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5.0)
        result_queue.close()
        result_queue.join_thread()


@pytest.mark.asyncio
async def test_fresh_schema_creates_canonical_idempotency_contract(
    tmp_path: Path,
) -> None:
    """新库必须创建唯一映射、冲突审计和三类维护 trigger。"""

    connection = await aiosqlite.connect(tmp_path / "memora.db")
    manager = SchemaManager(connection)
    await manager.create_fresh_schema()
    inspection = await manager.inspect_schema()
    await connection.close()

    assert CURRENT_DB_VERSION == 9
    assert {
        "canonical_idempotency_keys",
        "canonical_idempotency_conflicts",
    }.issubset(inspection.tables)
    assert {
        "documents_idempotency_insert",
        "documents_idempotency_update",
        "documents_idempotency_delete",
    }.issubset(inspection.triggers)


@pytest.mark.asyncio
async def test_v8_duplicate_keys_migrate_without_deleting_canonical(
    tmp_path: Path,
) -> None:
    """重复 key 以最小 ID 为 owner，其他 canonical 原样保留并留审计。"""

    db_path = tmp_path / "memora.db"
    original_rows = _create_v8_database(db_path)
    connection = await aiosqlite.connect(db_path)
    manager = SchemaManager(connection)
    inspection = await manager.inspect_schema()
    plan = manager.build_migration_plan(inspection)
    assert plan is not None

    await manager.migrate_existing_schema(plan)
    canonical_rows = await (
        await connection.execute("SELECT id, metadata FROM documents ORDER BY id")
    ).fetchall()
    mappings = await (
        await connection.execute(
            """
            SELECT idempotency_key, canonical_memory_id
            FROM canonical_idempotency_keys
            ORDER BY idempotency_key
            """
        )
    ).fetchall()
    conflicts = await (
        await connection.execute(
            """
            SELECT idempotency_key, owner_memory_id, duplicate_memory_id, resolution
            FROM canonical_idempotency_conflicts
            """
        )
    ).fetchall()
    migration_row = await (
        await connection.execute(
            "SELECT value FROM migration_status WHERE key = ?",
            (f"schema:{plan.migration_id}",),
        )
    ).fetchone()
    await connection.close()

    assert canonical_rows == original_rows
    assert mappings == [("retry-key", 1), ("unique-key", 3)]
    assert conflicts == [("retry-key", 1, 2, "preserved_non_owner")]
    assert migration_row is not None
    migration_summary = json.loads(str(migration_row[0]))
    assert migration_summary["idempotency_mapping_rebuilt"] is True
    assert migration_summary["idempotency_conflicts_preserved"] == 1


@pytest.mark.asyncio
async def test_v8_migration_matches_python_strip_for_unicode_whitespace(
    tmp_path: Path,
) -> None:
    """迁移规范化必须与调用方 Python ``str.strip()`` 语义一致。"""

    db_path = tmp_path / "memora.db"
    _create_v8_database(db_path)
    legacy = sqlite3.connect(db_path)
    try:
        legacy.execute(
            "UPDATE documents SET metadata = ? WHERE id = 1",
            (_metadata("\u00a0unicode-key\u3000", revision="revision-owner"),),
        )
        legacy.execute(
            "UPDATE documents SET metadata = ? WHERE id = 2",
            (_metadata("unicode-key", revision="revision-duplicate"),),
        )
        legacy.commit()
    finally:
        legacy.close()

    connection = await aiosqlite.connect(db_path)
    manager = SchemaManager(connection)
    plan = manager.build_migration_plan(await manager.inspect_schema())
    assert plan is not None
    await manager.migrate_existing_schema(plan)

    mappings = await (
        await connection.execute(
            """
            SELECT idempotency_key, canonical_memory_id
            FROM canonical_idempotency_keys
            WHERE idempotency_key = ?
            """,
            ("unicode-key",),
        )
    ).fetchall()
    conflicts = await (
        await connection.execute(
            """
            SELECT owner_memory_id, duplicate_memory_id
            FROM canonical_idempotency_conflicts
            WHERE idempotency_key = ?
            """,
            ("unicode-key",),
        )
    ).fetchall()
    await connection.close()

    assert mappings == [("unicode-key", 1)]
    assert conflicts == [(1, 2)]


@pytest.mark.asyncio
async def test_independent_connections_atomically_reuse_same_key(
    tmp_path: Path,
) -> None:
    """两个独立连接竞态写同 key 时只能产生一个 canonical ID。"""

    db_path = tmp_path / "memora.db"
    setup_connection = await aiosqlite.connect(db_path)
    await SchemaManager(setup_connection).create_fresh_schema()
    await setup_connection.close()

    first = await aiosqlite.connect(db_path)
    second = await aiosqlite.connect(db_path)
    try:
        await first.execute("PRAGMA busy_timeout = 10000")
        await second.execute("PRAGMA busy_timeout = 10000")
        barrier = asyncio.Barrier(2)
        results = await asyncio.gather(
            _insert_or_reuse(
                first,
                doc_id="engine-one",
                key=" connection-race ",
                barrier=barrier,
            ),
            _insert_or_reuse(
                second,
                doc_id="engine-two",
                key="connection-race",
                barrier=barrier,
            ),
        )
        count_row = await (
            await first.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()
    finally:
        await first.close()
        await second.close()

    assert {result[0] for result in results} == {1}
    assert sorted(result[1] for result in results) == [False, True]
    assert count_row == (1,)


@pytest.mark.asyncio
async def test_migration_failure_rolls_back_mapping_and_version(
    tmp_path: Path,
) -> None:
    """映射回填失败时表、trigger、版本与 canonical 必须整体回滚。"""

    db_path = tmp_path / "memora.db"
    original_rows = _create_v8_database(db_path)
    connection = await aiosqlite.connect(db_path)
    manager = SchemaManager(connection)
    plan = manager.build_migration_plan(await manager.inspect_schema())
    assert plan is not None

    with patch(
        "core.features.memory.infrastructure.schema_manager."
        "rebuild_canonical_idempotency_mapping",
        new=AsyncMock(side_effect=RuntimeError("injected idempotency rebuild failure")),
    ):
        with pytest.raises(RuntimeError, match="injected idempotency rebuild failure"):
            await manager.migrate_existing_schema(plan)

    tables = {
        str(row[0])
        for row in await (
            await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ).fetchall()
    }
    triggers = {
        str(row[0])
        for row in await (
            await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        ).fetchall()
    }
    version_row = await (
        await connection.execute("SELECT MAX(version) FROM db_version")
    ).fetchone()
    canonical_rows = await (
        await connection.execute("SELECT id, metadata FROM documents ORDER BY id")
    ).fetchall()
    await connection.close()

    assert "canonical_idempotency_keys" not in tables
    assert "canonical_idempotency_conflicts" not in tables
    assert not any(name.startswith("documents_idempotency_") for name in triggers)
    assert version_row == (8,)
    assert canonical_rows == original_rows


@pytest.mark.asyncio
async def test_migration_cancellation_rolls_back_and_propagates(
    tmp_path: Path,
) -> None:
    """迁移取消必须传播，并保留 v8 canonical 与旧 Schema。"""

    db_path = tmp_path / "memora.db"
    original_rows = _create_v8_database(db_path)
    connection = await aiosqlite.connect(db_path)
    manager = SchemaManager(connection)
    plan = manager.build_migration_plan(await manager.inspect_schema())
    assert plan is not None

    with patch(
        "core.features.memory.infrastructure.schema_manager."
        "rebuild_canonical_idempotency_mapping",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await manager.migrate_existing_schema(plan)

    tables = {
        str(row[0])
        for row in await (
            await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ).fetchall()
    }
    canonical_rows = await (
        await connection.execute("SELECT id, metadata FROM documents ORDER BY id")
    ).fetchall()
    await connection.close()

    assert "canonical_idempotency_keys" not in tables
    assert canonical_rows == original_rows


@pytest.mark.asyncio
async def test_deleting_migration_owner_promotes_next_preserved_canonical(
    tmp_path: Path,
) -> None:
    """删除 owner 时 trigger 必须把映射提升到最小剩余重复 canonical。"""

    db_path = tmp_path / "memora.db"
    _create_v8_database(db_path)
    connection = await aiosqlite.connect(db_path)
    manager = SchemaManager(connection)
    plan = manager.build_migration_plan(await manager.inspect_schema())
    assert plan is not None
    await manager.migrate_existing_schema(plan)

    await connection.execute("DELETE FROM documents WHERE id = 1")
    await connection.commit()
    owner_id = await find_canonical_memory_id_by_idempotency_key(
        connection,
        " retry-key ",
    )
    duplicate_row = await (
        await connection.execute("SELECT metadata FROM documents WHERE id = 2")
    ).fetchone()
    await connection.close()

    assert owner_id == 2
    assert duplicate_row == (_metadata("retry-key", revision="revision-duplicate"),)


@pytest.mark.asyncio
async def test_spawn_processes_atomically_reuse_same_key(tmp_path: Path) -> None:
    """两个独立进程竞态时一个提交、另一个返回同一 owner ID。"""

    db_path = tmp_path / "memora.db"
    connection = await aiosqlite.connect(db_path)
    await SchemaManager(connection).create_fresh_schema()
    await connection.close()

    results = await asyncio.to_thread(_run_multiprocess_race, db_path)
    verification = sqlite3.connect(db_path)
    try:
        canonical_count = int(
            verification.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        )
        mapping_count = int(
            verification.execute(
                "SELECT COUNT(*) FROM canonical_idempotency_keys"
            ).fetchone()[0]
        )
    finally:
        verification.close()

    assert all(result[2] is None for result in results)
    assert {result[0] for result in results} == {1}
    assert sorted(result[1] for result in results) == [False, True]
    assert canonical_count == 1
    assert mapping_count == 1
