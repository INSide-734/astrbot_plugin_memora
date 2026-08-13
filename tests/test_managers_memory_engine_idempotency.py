"""MemoryEngine canonical 幂等预检、竞态恢复与取消契约测试。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.infrastructure.canonical_idempotency import (
    create_canonical_idempotency_schema,
)


async def _create_idempotency_test_schema(
    connection: aiosqlite.Connection,
) -> None:
    """创建 MemoryEngine 幂等测试所需的最小 v9 canonical schema。"""

    await connection.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
        """
    )
    await create_canonical_idempotency_schema(connection)
    await connection.commit()


def _configure_idempotent_write_engine(
    engine: MemoryEngine,
    add_document: Callable[[str, dict[str, object]], Awaitable[int]],
) -> None:
    """为测试装配最小 canonical 写链，隔离无关派生组件。"""

    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.add_memory = add_document
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._retrieval.apply_interference = MagicMock(return_value=None)
    engine._retrieval.extract_triggers = MagicMock(return_value=None)
    engine._create_tracked_task = MagicMock()


class TestMemoryEngineIdempotency:
    """验证 canonical 幂等键的引擎级契约。"""

    @pytest.mark.asyncio
    async def test_add_memory_reuses_existing_idempotency_key(self) -> None:
        """Unicode 空白规范化后的重试不得发起第二次写入。"""

        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        engine.find_memory_id_by_idempotency_key = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[None, 7]
        )
        engine._add_memory_unchecked = AsyncMock(return_value=7)  # type: ignore[attr-defined]
        metadata = {"idempotency_key": "\u00a0reflection-key\u3000"}

        first = await engine.add_memory("记忆正文", metadata=metadata)
        second = await engine.add_memory("记忆正文", metadata=metadata)

        assert (first, second) == (7, 7)
        engine._add_memory_unchecked.assert_awaited_once()  # type: ignore[attr-defined]
        assert engine._add_memory_unchecked.await_args.kwargs["metadata"] == {  # type: ignore[attr-defined]
            "idempotency_key": "reflection-key"
        }
        assert [
            call.args[0]
            for call in engine.find_memory_id_by_idempotency_key.await_args_list  # type: ignore[attr-defined]
        ] == ["reflection-key", "reflection-key"]

    @pytest.mark.asyncio
    async def test_durable_idempotency_lookup_reads_v9_mapping(
        self,
        tmp_db_path: str,
    ) -> None:
        """引擎查询必须读取 v9 映射，并复用 Unicode key 规范化语义。"""

        engine = MemoryEngine(db_path=tmp_db_path, faiss_db=MagicMock())
        engine.db_connection = await aiosqlite.connect(tmp_db_path)
        try:
            await _create_idempotency_test_schema(engine.db_connection)
            await engine.db_connection.execute(
                "INSERT INTO documents(id, text, metadata) VALUES(?, ?, ?)",
                (
                    7,
                    "canonical",
                    json.dumps({"idempotency_key": "durable-key"}),
                ),
            )
            await engine.db_connection.commit()

            assert (
                await engine.find_memory_id_by_idempotency_key(
                    "\u00a0durable-key\u3000"
                )
                == 7
            )
            assert await engine.find_memory_id_by_idempotency_key("missing") is None
        finally:
            await engine.db_connection.close()

    @pytest.mark.asyncio
    async def test_durable_idempotency_lookup_rejects_orphan_mapping(
        self,
        tmp_db_path: str,
    ) -> None:
        """孤立 v9 映射必须 fail-closed，不能伪造 canonical owner。"""

        engine = MemoryEngine(db_path=tmp_db_path, faiss_db=MagicMock())
        engine.db_connection = await aiosqlite.connect(tmp_db_path)
        try:
            await _create_idempotency_test_schema(engine.db_connection)
            await engine.db_connection.execute(
                """
                INSERT INTO canonical_idempotency_keys (
                    idempotency_key, canonical_memory_id, created_at
                ) VALUES (?, ?, ?)
                """,
                ("orphan-key", 404, "2026-08-05T00:00:00Z"),
            )
            await engine.db_connection.commit()

            with pytest.raises(
                RuntimeError,
                match="canonical_idempotency_mapping_invalid",
            ):
                await engine.find_memory_id_by_idempotency_key("orphan-key")
        finally:
            await engine.db_connection.close()

    @pytest.mark.asyncio
    async def test_concurrent_add_memory_serializes_same_idempotency_key(self) -> None:
        """同一引擎内的并发重试只允许创建一条 canonical。"""

        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        persisted_id: int | None = None
        write_count = 0

        async def find_existing(_key: str) -> int | None:
            """返回当前测试进程内已提交的 canonical ID。"""

            return persisted_id

        async def write_once(*args, **kwargs) -> int:
            """模拟单次 canonical 写入并让出一次调度。"""

            nonlocal persisted_id, write_count
            write_count += 1
            await asyncio.sleep(0)
            persisted_id = 9
            return persisted_id

        engine.find_memory_id_by_idempotency_key = find_existing  # type: ignore[attr-defined]
        engine._add_memory_unchecked = write_once  # type: ignore[attr-defined]
        metadata = {"idempotency_key": "concurrent-reflection-key"}

        results = await asyncio.gather(
            engine.add_memory("记忆正文", metadata=metadata),
            engine.add_memory("记忆正文", metadata=metadata),
        )

        assert results == [9, 9]
        assert write_count == 1

    @pytest.mark.asyncio
    async def test_independent_engines_racing_reuse_one_canonical_owner(
        self,
        tmp_db_path: str,
    ) -> None:
        """独立引擎与连接竞态时，唯一约束 loser 返回 winner 的 canonical ID。"""

        setup_connection = await aiosqlite.connect(tmp_db_path)
        await _create_idempotency_test_schema(setup_connection)
        await setup_connection.close()

        winner_connection = await aiosqlite.connect(tmp_db_path)
        loser_connection = await aiosqlite.connect(tmp_db_path)
        barrier = asyncio.Barrier(2)
        winner_committed = asyncio.Event()

        async def winner_add(content: str, metadata: dict[str, object]) -> int:
            """等待双方通过写前预检后，提交唯一 canonical owner。"""

            await barrier.wait()
            cursor = await winner_connection.execute(
                "INSERT INTO documents(text, metadata) VALUES(?, ?)",
                (content, json.dumps(metadata)),
            )
            await winner_connection.commit()
            winner_committed.set()
            return int(cursor.lastrowid)

        async def loser_add(content: str, metadata: dict[str, object]) -> int:
            """在 winner 提交后触发 v9 唯一映射约束。"""

            await barrier.wait()
            await winner_committed.wait()
            await loser_connection.execute(
                "INSERT INTO documents(text, metadata) VALUES(?, ?)",
                (content, json.dumps(metadata)),
            )
            raise AssertionError("v9 唯一映射约束未拒绝竞态 loser")

        winner = MemoryEngine(db_path=tmp_db_path, faiss_db=MagicMock())
        loser = MemoryEngine(db_path=tmp_db_path, faiss_db=MagicMock())
        winner.db_connection = winner_connection
        loser.db_connection = loser_connection
        _configure_idempotent_write_engine(winner, winner_add)
        _configure_idempotent_write_engine(loser, loser_add)

        try:
            results = await asyncio.gather(
                winner.add_memory(
                    "竞态记忆",
                    metadata={"idempotency_key": "cross-engine-key"},
                ),
                loser.add_memory(
                    "竞态记忆",
                    metadata={"idempotency_key": "cross-engine-key"},
                ),
            )
            row = await (
                await winner_connection.execute("SELECT COUNT(*) FROM documents")
            ).fetchone()
        finally:
            await winner_connection.close()
            await loser_connection.close()

        assert results[0] == results[1]
        assert row == (1,)
        loser._write_journal.advance_op.assert_awaited_once_with(
            1,
            "completed",
            status="completed",
            memory_id=results[0],
            payload_patch={"memory_id": results[0]},
        )

    @pytest.mark.asyncio
    async def test_write_error_without_owner_reraises_original_exception(self) -> None:
        """重查一次仍无映射时，必须保留原写入异常对象与 traceback。"""

        original_error = sqlite3.IntegrityError("unrelated constraint")
        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        _configure_idempotent_write_engine(
            engine,
            AsyncMock(side_effect=original_error),
        )
        engine.find_memory_id_by_idempotency_key = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[None, None]
        )

        with pytest.raises(sqlite3.IntegrityError) as raised:
            await engine.add_memory(
                "记忆正文",
                metadata={"idempotency_key": "missing-owner"},
            )

        assert raised.value is original_error
        assert engine.find_memory_id_by_idempotency_key.await_count == 2  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_orphan_recovery_lookup_does_not_hide_write_error(self) -> None:
        """恢复查询发现孤立映射时，不得把无关写错误转换为成功。"""

        original_error = sqlite3.IntegrityError("unrelated constraint")
        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        _configure_idempotent_write_engine(
            engine,
            AsyncMock(side_effect=original_error),
        )
        engine.find_memory_id_by_idempotency_key = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[
                None,
                RuntimeError("canonical_idempotency_mapping_invalid"),
            ]
        )

        with pytest.raises(sqlite3.IntegrityError) as raised:
            await engine.add_memory(
                "记忆正文",
                metadata={"idempotency_key": "orphan-owner"},
            )

        assert raised.value is original_error
        assert engine.find_memory_id_by_idempotency_key.await_count == 2  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_cancelled_write_does_not_recheck_owner(self) -> None:
        """canonical 写入取消必须原样传播，且不得进入 owner 恢复查询。"""

        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        _configure_idempotent_write_engine(
            engine,
            AsyncMock(side_effect=asyncio.CancelledError()),
        )
        engine.find_memory_id_by_idempotency_key = AsyncMock(  # type: ignore[attr-defined]
            return_value=None
        )

        with pytest.raises(asyncio.CancelledError):
            await engine.add_memory(
                "记忆正文",
                metadata={"idempotency_key": "cancelled-write"},
            )

        engine.find_memory_id_by_idempotency_key.assert_awaited_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_cancelled_owner_recheck_propagates(self) -> None:
        """写入失败后的 owner 查询若被取消，取消必须覆盖恢复流程。"""

        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        _configure_idempotent_write_engine(
            engine,
            AsyncMock(side_effect=sqlite3.IntegrityError("key conflict")),
        )
        engine.find_memory_id_by_idempotency_key = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[None, asyncio.CancelledError()]
        )

        with pytest.raises(asyncio.CancelledError):
            await engine.add_memory(
                "记忆正文",
                metadata={"idempotency_key": "cancelled-recheck"},
            )

        assert engine.find_memory_id_by_idempotency_key.await_count == 2  # type: ignore[attr-defined]
