"""Concurrent write stress tests for shared MemoryEngine write paths."""

from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest

from core.features.memory.infrastructure.base import apply_perf_pragmas
from core.managers.decay_operations import DecayOperationsMixin


class _DecayHost(DecayOperationsMixin):
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._config = {}
        self._db = db
        self._invalidate_cache = None


@pytest.mark.asyncio
async def test_concurrent_batch_access_updates_do_not_lose_increments(
    tmp_db_path: str,
) -> None:
    db = await aiosqlite.connect(tmp_db_path)
    db.row_factory = aiosqlite.Row
    await apply_perf_pragmas(db)
    await db.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT UNIQUE,
            content TEXT,
            metadata TEXT
        )
        """
    )
    for index in range(1, 6):
        await db.execute(
            "INSERT INTO documents (doc_id, content, metadata) VALUES (?, ?, ?)",
            (
                f"doc-{index}",
                f"memory {index}",
                json.dumps({"importance": 0.5, "access_count": 0}),
            ),
        )
    await db.commit()

    host = _DecayHost(db)
    task_count = 20
    results = await asyncio.gather(
        *[
            host.update_access_times_batch([1, 2, 3, 4, 5], recall_type="passive")
            for _ in range(task_count)
        ]
    )

    assert results == [5] * task_count
    cursor = await db.execute("SELECT id, metadata FROM documents ORDER BY id")
    rows = await cursor.fetchall()
    await cursor.close()
    await db.close()

    for row in rows:
        metadata = json.loads(row["metadata"])
        assert metadata["access_count"] == task_count
        assert metadata["importance"] == 0.7
