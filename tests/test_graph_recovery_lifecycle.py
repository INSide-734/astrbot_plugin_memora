"""图派生持久操作的就绪屏障与启动恢复行为回归。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.graph.domain.models import ExtractedGraph, GraphEntry
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.memory.infrastructure.schema_manager import SchemaManager
from core.features.memory.infrastructure.write_op_journal import WriteOpJournal
from core.features.reconsolidation.application.reconsolidation import (
    ReconsolidationManager,
)
from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever


class _SqliteDocumentStorage:
    """用真实临时 SQLite 模拟宿主文档存储的 engine 就绪边界。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.engine: object | None = None

    async def initialize(self) -> None:
        self.engine = object()

    async def get_documents(
        self,
        *,
        metadata_filters: dict[str, Any],
        ids: list[int] | None = None,
        offset: int | None = 0,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        if self.engine is None:
            return []
        if metadata_filters:
            raise AssertionError("canonical 测试存储不接受 metadata 过滤")
        query = (
            "SELECT id, doc_id, text, metadata, created_at, updated_at FROM documents"
        )
        params: list[Any] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            query += f" WHERE id IN ({placeholders})"
            params.extend(ids)
        query += " ORDER BY id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None:
                query += " OFFSET ?"
                params.append(offset)
        async with aiosqlite.connect(self.db_path) as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [
            {
                "id": int(row[0]),
                "doc_id": row[1],
                "text": row[2],
                "metadata": json.loads(row[3] or "{}"),
                "created_at": row[4],
                "updated_at": row[5],
            }
            for row in rows
        ]

    async def close(self) -> None:
        """释放临时文档存储的就绪状态。"""

        self.engine = None


class _CanonicalVectorDb:
    """只暴露生命周期恢复所需的 canonical 文档存储。"""

    def __init__(self, db_path: Path) -> None:
        self.document_storage = _SqliteDocumentStorage(db_path)

    async def close(self) -> None:
        """关闭 canonical 文档存储替身。"""

        await self.document_storage.close()


class _GraphDocumentStorage:
    """读取合成图向量记录，并复刻宿主未就绪时返回空列表的行为。"""

    def __init__(self, owner: _RejectingGraphVectorDb) -> None:
        self.owner = owner
        self.engine: object | None = None

    async def initialize(self) -> None:
        self.engine = object()

    async def get_documents(
        self,
        *,
        metadata_filters: dict[str, Any],
        ids: list[int] | None = None,
        offset: int | None = 0,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        if self.engine is None:
            return []
        records = list(self.owner.records.values())
        if ids:
            wanted = {int(item) for item in ids}
            records = [item for item in records if int(item["id"]) in wanted]
        for key, value in metadata_filters.items():
            records = [item for item in records if item["metadata"].get(key) == value]
        start = int(offset or 0)
        stop = None if limit is None else start + int(limit)
        return [dict(item) for item in records[start:stop]]

    async def close(self) -> None:
        """释放图文档存储的就绪状态。"""

        self.engine = None


class _RejectingGraphVectorDb:
    """按 batch_size 切 embedding 请求，并拒绝任何超过十条的子请求。"""

    def __init__(self) -> None:
        self.document_storage = _GraphDocumentStorage(self)
        self.records: dict[int, dict[str, Any]] = {}
        self.embedding_request_sizes: list[int] = []
        self.next_id = 1000

    async def initialize(self) -> None:
        await self.document_storage.initialize()

    async def insert_batch(
        self,
        *,
        contents: list[str],
        metadatas: list[dict[str, Any]],
        batch_size: int,
    ) -> list[int]:
        vector_ids: list[int] = []
        for start in range(0, len(contents), batch_size):
            request_contents = contents[start : start + batch_size]
            request_metadata = metadatas[start : start + batch_size]
            if len(request_contents) > 10:
                raise RuntimeError("embedding_request_too_large")
            self.embedding_request_sizes.append(len(request_contents))
            for content, metadata in zip(
                request_contents,
                request_metadata,
                strict=True,
            ):
                vector_id = self.next_id
                self.next_id += 1
                vector_ids.append(vector_id)
                self.records[vector_id] = {
                    "id": vector_id,
                    "doc_id": f"graph-{vector_id}",
                    "text": content,
                    "metadata": dict(metadata),
                }
        return vector_ids

    async def delete(self, document_id: str) -> bool:
        for vector_id, record in tuple(self.records.items()):
            if record["doc_id"] == document_id:
                del self.records[vector_id]
                return True
        return False

    async def close(self) -> None:
        """关闭图向量后端替身。"""

        await self.document_storage.close()


class _StaticGraphExtractor:
    """为恢复场景生成十一条顺序稳定的图条目。"""

    def extract(
        self,
        source_memory_id: int,
        _content: str,
        metadata: dict[str, Any] | None,
        _atoms: list[Any] | None,
    ) -> ExtractedGraph:
        metadata = metadata or {}
        return ExtractedGraph(
            entries=[
                GraphEntry(
                    entry_key=f"entry-{source_memory_id}-{index}",
                    source_memory_id=source_memory_id,
                    session_id=metadata.get("session_id"),
                    persona_id=metadata.get("persona_id"),
                    entry_type="fact",
                    content=f"graph-entry-{index}",
                    metadata={"rank": index},
                )
                for index in range(11)
            ]
        )


def _engine_config(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "graph_memory_enabled": False,
        "recall_engine.stopwords_path": "",
        "write_reliability.repair_enabled": True,
        "user_profile.enabled": False,
        "auto_learning.enabled": False,
        "knowledge_base.enabled": False,
        "notes.enabled": False,
        "reranker.enabled": False,
        "export.enabled": False,
        "continuity_tracking.enabled": False,
        "reconsolidation.enabled": False,
        "anomaly_detection.enabled": False,
    }
    config.update(overrides)
    return config


async def _insert_document(
    db: aiosqlite.Connection,
    memory_id: int,
    *,
    content: str,
) -> None:
    metadata = json.dumps(
        {"session_id": "session-a", "persona_id": "persona-a"},
        ensure_ascii=False,
    )
    await db.execute(
        """
        INSERT INTO documents(id, doc_id, text, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (memory_id, f"doc-{memory_id}", content, metadata, "r1", "r1"),
    )
    await db.commit()


async def _operation_rows(db: aiosqlite.Connection) -> list[tuple[Any, ...]]:
    cursor = await db.execute(
        """
        SELECT id, op_type, memory_id, status, step, retry_count
        FROM memory_write_ops ORDER BY id
        """
    )
    return [tuple(row) for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_initialize_defers_journal_and_reconsolidation_recovery(
    tmp_path: Path,
) -> None:
    """initialize 只构造功能；显式恢复入口在文档引擎就绪后才执行。"""

    canonical_db = _CanonicalVectorDb(tmp_path / "memora.db")
    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=canonical_db,
        config=_engine_config(tmp_path, **{"reconsolidation.enabled": True}),
    )
    journal_repair = AsyncMock()
    engine._write_journal.repair_incomplete = journal_repair
    apply_recovery = AsyncMock()
    rollback_recovery = AsyncMock()
    try:
        with (
            patch(
                "core.features.memory.application.memory_engine_lifecycle.BM25Retriever"
            ) as bm25_cls,
            patch.object(
                ReconsolidationManager,
                "recover_incomplete_applies",
                apply_recovery,
            ),
            patch.object(
                ReconsolidationManager,
                "recover_incomplete_rollbacks",
                rollback_recovery,
            ),
        ):
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

            journal_repair.assert_not_awaited()
            apply_recovery.assert_not_awaited()
            rollback_recovery.assert_not_awaited()

            await canonical_db.document_storage.initialize()
            await engine.recover_persisted_operations()

        journal_repair.assert_awaited_once()
        apply_recovery.assert_awaited_once()
        rollback_recovery.assert_awaited_once()
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_ready_gated_recovery_restores_graph_without_changing_canonical(
    tmp_path: Path,
) -> None:
    """两类文档引擎未就绪时零副作用，就绪后恢复图映射且保持 canonical。"""

    db_path = tmp_path / "memora.db"
    canonical_db = _CanonicalVectorDb(db_path)
    graph_db = _RejectingGraphVectorDb()
    engine = MemoryEngine(
        db_path=str(db_path),
        faiss_db=canonical_db,
        graph_vector_db=graph_db,
        config=_engine_config(
            tmp_path,
            **{
                "graph_memory_enabled": True,
                "write_reliability.max_retries": 3,
            },
        ),
    )
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    cast(Any, engine).db_connection = db
    engine._write_journal._db = db
    engine._write_journal._topic_catalog_store = None
    await SchemaManager(db).create_tables(engine._write_journal.create_table)
    await _insert_document(db, 7, content="canonical-content")
    canonical_before_row = await (
        await db.execute(
            """
            SELECT id, doc_id, text, metadata, created_at, updated_at
            FROM documents WHERE id = 7
            """
        )
    ).fetchone()
    assert canonical_before_row is not None
    canonical_before = tuple(canonical_before_row)
    op_id = await engine._write_journal.start_op("add", memory_id=7)
    await db.execute(
        """
        UPDATE memory_write_ops
        SET status = 'failed', step = 'source_missing', retry_count = 1
        WHERE id = ?
        """,
        (op_id,),
    )
    await db.commit()

    graph_store = GraphStore(str(db_path))
    await graph_store.initialize()
    graph_retriever = GraphVectorRetriever(graph_db)
    graph_manager = GraphMemoryManager(
        graph_store,
        graph_retriever,
        cast(Any, _StaticGraphExtractor()),
    )
    engine._write_journal._graph_memory_manager = graph_manager
    reconsolidation = MagicMock()
    reconsolidation.recover_incomplete_applies = AsyncMock()
    reconsolidation.recover_incomplete_rollbacks = AsyncMock()
    cast(Any, engine).reconsolidation = reconsolidation

    operations_before = await _operation_rows(db)
    graph_before = await graph_store.get_memory_entry_stats()
    try:
        with pytest.raises(
            RuntimeError,
            match="persisted_recovery_canonical_document_storage_not_ready",
        ):
            await engine.recover_persisted_operations()
        assert await _operation_rows(db) == operations_before
        assert await graph_store.get_memory_entry_stats() == graph_before
        reconsolidation.recover_incomplete_applies.assert_not_awaited()

        await canonical_db.document_storage.initialize()
        with pytest.raises(
            RuntimeError,
            match="persisted_recovery_graph_document_storage_not_ready",
        ):
            await engine.recover_persisted_operations()
        assert await _operation_rows(db) == operations_before
        assert await graph_store.get_memory_entry_stats() == graph_before
        reconsolidation.recover_incomplete_applies.assert_not_awaited()

        await graph_db.initialize()
        await engine.recover_persisted_operations()

        operation = await (
            await db.execute(
                "SELECT status, step, retry_count FROM memory_write_ops WHERE id = ?",
                (op_id,),
            )
        ).fetchone()
        assert operation is not None
        assert tuple(operation) == ("completed", "completed", 1)
        graph_rows = await (
            await db.execute(
                """
                SELECT content, vector_doc_id FROM graph_entries
                WHERE source_memory_id = 7 ORDER BY id
                """
            )
        ).fetchall()
        assert [tuple(row) for row in graph_rows] == [
            (f"graph-entry-{index}", 1000 + index) for index in range(11)
        ]
        assert graph_db.embedding_request_sizes == [10, 1]
        for index, (_content, vector_id) in enumerate(graph_rows):
            record = graph_db.records[int(vector_id)]
            assert record["text"] == f"graph-entry-{index}"
            assert record["metadata"] == {
                "rank": index,
                "source_memory_id": 7,
            }
        canonical_after_row = await (
            await db.execute(
                """
                SELECT id, doc_id, text, metadata, created_at, updated_at
                FROM documents WHERE id = 7
                """
            )
        ).fetchone()
        assert canonical_after_row is not None
        assert tuple(canonical_after_row) == canonical_before
        canonical_count_row = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()
        assert canonical_count_row is not None
        assert canonical_count_row[0] == 1

        graph_after = await graph_store.get_memory_entry_stats()
        vector_records_after = dict(graph_db.records)
        await engine.recover_persisted_operations()
        assert await graph_store.get_memory_entry_stats() == graph_after
        assert graph_db.records == vector_records_after
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repair_reopens_only_eligible_source_missing_operations(
    tmp_path: Path,
) -> None:
    """只重纳有 canonical、未耗尽预算的 add/graph_reindex source_missing。"""

    db_path = tmp_path / "memora.db"
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    canonical_db = _CanonicalVectorDb(db_path)
    await canonical_db.document_storage.initialize()
    graph_manager = MagicMock()
    graph_manager.index_memory = AsyncMock()

    async def get_memory(memory_id: int) -> dict[str, Any] | None:
        documents = await canonical_db.document_storage.get_documents(
            metadata_filters={},
            ids=[memory_id],
            limit=1,
        )
        return documents[0] if documents else None

    journal = WriteOpJournal(
        db,
        graph_manager,
        atom_store=None,
        write_op_max_retries=3,
        get_memory_cb=get_memory,
    )
    await SchemaManager(db).create_tables(journal.create_table)
    for memory_id in range(1, 5):
        await _insert_document(db, memory_id, content=f"canonical-{memory_id}")

    cases = (
        ("graph_reindex", 1, "source_missing", 1),
        ("add", 999, "source_missing", 1),
        ("add", 2, "source_stale", 1),
        ("add", 3, "source_missing", 3),
        ("delete", 4, "source_missing", 1),
    )
    op_ids: list[int] = []
    for op_type, memory_id, step, retry_count in cases:
        op_id = await journal.start_op(op_type, memory_id=memory_id)
        assert op_id is not None
        op_ids.append(op_id)
        await db.execute(
            """
            UPDATE memory_write_ops
            SET status = 'failed', step = ?, retry_count = ?
            WHERE id = ?
            """,
            (step, retry_count, op_id),
        )
    await db.commit()

    try:
        assert await journal.repair_incomplete() == 1
        graph_manager.index_memory.assert_awaited_once_with(
            1,
            "canonical-1",
            {"session_id": "session-a", "persona_id": "persona-a"},
            None,
        )
        rows = await _operation_rows(db)
        by_id = {int(row[0]): row for row in rows}
        assert by_id[op_ids[0]][3:] == ("completed", "completed", 1)
        assert by_id[op_ids[1]][3:] == ("failed", "source_missing", 1)
        assert by_id[op_ids[2]][3:] == ("failed", "source_stale", 1)
        assert by_id[op_ids[3]][3:] == ("failed", "source_missing", 3)
        assert by_id[op_ids[4]][3:] == ("failed", "source_missing", 1)
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_stage", ("journal", "apply", "rollback"))
async def test_persisted_recovery_propagates_cancellation(
    tmp_path: Path,
    cancelled_stage: str,
) -> None:
    """journal、apply 与 rollback 任一恢复阶段被取消时均立即传播。"""

    canonical_db = _CanonicalVectorDb(tmp_path / "memora.db")
    await canonical_db.document_storage.initialize()
    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=canonical_db,
        config=_engine_config(tmp_path),
    )
    engine._write_journal.repair_incomplete = AsyncMock()
    reconsolidation = MagicMock()
    reconsolidation.recover_incomplete_applies = AsyncMock()
    reconsolidation.recover_incomplete_rollbacks = AsyncMock()
    cast(Any, engine).reconsolidation = reconsolidation
    if cancelled_stage == "journal":
        engine._write_journal.repair_incomplete.side_effect = asyncio.CancelledError
    elif cancelled_stage == "apply":
        reconsolidation.recover_incomplete_applies.side_effect = asyncio.CancelledError
    else:
        reconsolidation.recover_incomplete_rollbacks.side_effect = (
            asyncio.CancelledError
        )

    with pytest.raises(asyncio.CancelledError):
        await engine.recover_persisted_operations()

    if cancelled_stage == "journal":
        reconsolidation.recover_incomplete_applies.assert_not_awaited()
    if cancelled_stage in {"journal", "apply"}:
        reconsolidation.recover_incomplete_rollbacks.assert_not_awaited()
