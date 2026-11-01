"""共享连接在旧 WAL 快照上写入失败后不得滞留在半开事务。

复现生产缺陷：共享连接上另一协程的读语句未收束时，其它连接提交会让本连接
升级写事务立即得到 ``database is locked``。旧实现以隐式事务写写操作日志，失败
后连接停在 ``in_transaction``，此后所有写入（来源激活、目录收敛）都立即失败。
"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from core.features.memory.infrastructure.schema_manager import SchemaManager
from core.features.memory.infrastructure.write_op_journal import WriteOpJournal


async def _open_pair(path: str) -> tuple[aiosqlite.Connection, aiosqlite.Connection]:
    """打开共享连接与外部连接，WAL 模式下才会出现旧快照写冲突。"""

    shared = await aiosqlite.connect(path)
    shared.row_factory = aiosqlite.Row
    other = await aiosqlite.connect(path)
    for connection in (shared, other):
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=2000")
    return shared, other


async def _pin_stale_snapshot(
    shared: aiosqlite.Connection, other: aiosqlite.Connection
) -> aiosqlite.Cursor:
    """让共享连接持有未收束的读语句，再由外部连接推进 WAL。"""

    for value in ("a", "b", "c"):
        await shared.execute(
            "INSERT INTO migration_status (key, value, updated_at) VALUES (?, ?, 0)",
            (value, value),
        )
    await shared.commit()
    reader = await shared.execute("SELECT key FROM migration_status")
    await reader.fetchone()
    await other.execute("UPDATE migration_status SET value = 'z' WHERE key = 'c'")
    await other.commit()
    return reader


@pytest.mark.asyncio
async def test_journal_write_retries_until_stale_reader_finishes(
    tmp_db_path: str,
) -> None:
    shared, other = await _open_pair(tmp_db_path)
    try:
        journal = WriteOpJournal(
            db_connection=shared, graph_memory_manager=None, atom_store=None
        )
        await SchemaManager(shared).create_tables(journal.create_table)
        reader = await _pin_stale_snapshot(shared, other)

        async def finish_reader() -> None:
            await asyncio.sleep(0.08)
            await reader.close()

        closer = asyncio.create_task(finish_reader())
        op_id = await journal.start_op("add", {"k": "v"})
        await closer

        assert op_id is not None
        assert shared.in_transaction is False
        await journal.advance_op(op_id, "completed", status="completed")
        cursor = await other.execute(
            "SELECT status FROM memory_write_ops WHERE id = ?", (op_id,)
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == "completed"
    finally:
        await shared.close()
        await other.close()


@pytest.mark.asyncio
async def test_exhausted_journal_write_leaves_connection_usable(
    tmp_db_path: str,
) -> None:
    shared, other = await _open_pair(tmp_db_path)
    try:
        journal = WriteOpJournal(
            db_connection=shared, graph_memory_manager=None, atom_store=None
        )
        await SchemaManager(shared).create_tables(journal.create_table)
        reader = await _pin_stale_snapshot(shared, other)

        assert await journal.start_op("add", {"k": "v"}) is None
        # 失败的写入不得把连接留在半开事务：读者收束后同一连接立即可写。
        assert shared.in_transaction is False
        await reader.close()
        op_id = await journal.start_op("add", {"k": "v"})
        assert op_id is not None
    finally:
        await shared.close()
        await other.close()
