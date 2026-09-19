"""验证 canonical metadata 更新使用 SQLite 原子 revision 比较。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.features.memory.application.atom_source_binding import (
    bind_atoms_to_canonical_source,
    validate_bound_atoms_match_canonical_source,
)
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.retrieval.hybrid_retriever import HybridRetriever
from core.features.retrieval.rrf_fusion import RRFFusion
from core.features.retrieval.vector_retriever import VectorRetriever
from tests.fact_evidence_helpers import source_evidence


class _DocumentStorage:
    """为 revision CAS 测试提供最小 SQLAlchemy 会话边界。"""

    def __init__(self, db_path: str) -> None:
        """创建指向临时 SQLite 的异步引擎。"""

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_session(self):
        """生成独立异步会话并在退出时关闭。"""

        async with self._sessions() as session:
            yield session

    async def close(self) -> None:
        """释放测试引擎。"""

        await self.engine.dispose()

    async def get_documents(
        self,
        metadata_filters: dict[str, Any] | None = None,
        ids: list[int] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """按 ID 读取最小 canonical 记录，供引擎详情与维护写入使用。"""

        async with self.get_session() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, text, metadata, created_at, updated_at "
                            "FROM documents ORDER BY id"
                        )
                    )
                )
                .mappings()
                .all()
            )
        documents = [
            {
                "id": int(row["id"]),
                "text": str(row["text"]),
                "metadata": json.loads(row["metadata"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
        if ids:
            wanted = {int(item) for item in ids}
            documents = [doc for doc in documents if doc["id"] in wanted]
        if offset:
            documents = documents[int(offset) :]
        if limit is not None:
            documents = documents[: int(limit)]
        return documents


class _EmbeddingStorage:
    """记录正文 CAS 测试中的派生向量替换。"""

    dimension = 3

    def __init__(self) -> None:
        """初始化操作记录。"""

        self.deleted: list[list[int]] = []
        self.inserted: list[int] = []

    async def delete(self, ids: list[int]) -> None:
        """记录删除旧向量。"""

        self.deleted.append(ids)

    async def insert(self, vector, doc_id: int) -> None:
        """记录插入新向量。"""

        self.inserted.append(doc_id)


async def _create_document(storage: _DocumentStorage) -> None:
    """建立最小 documents 表并写入固定 revision。"""

    async with storage.engine.begin() as connection:
        await connection.execute(
            text(
                """CREATE TABLE documents (
                       id INTEGER PRIMARY KEY,
                       text TEXT NOT NULL,
                       metadata TEXT,
                       created_at TEXT,
                       updated_at TEXT
                   )"""
            )
        )
        await connection.execute(
            text(
                """INSERT INTO documents
                   (id,text,metadata,created_at,updated_at)
                   VALUES (17,'匿名正文',:metadata,'rev-created','rev-current')"""
            ),
            {"metadata": json.dumps({"importance": 0.5})},
        )


@pytest.mark.asyncio
async def test_same_revision_allows_only_one_metadata_update(tmp_path) -> None:
    """两个 stale writer 竞争同一 revision 时只能有一个提交。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-cas.db"))
    await _create_document(storage)
    retriever = VectorRetriever(SimpleNamespace(document_storage=storage))

    results = await asyncio.gather(
        retriever.update_metadata(
            17,
            {"winner": "first"},
            expected_revision="rev-current",
        ),
        retriever.update_metadata(
            17,
            {"winner": "second"},
            expected_revision="rev-current",
        ),
    )

    assert sorted(results) == [False, True]
    async with storage.get_session() as session:
        row = (
            (
                await session.execute(
                    text("SELECT metadata, updated_at FROM documents WHERE id = 17")
                )
            )
            .mappings()
            .one()
        )
    stored = json.loads(row["metadata"])
    assert stored["winner"] in {"first", "second"}
    assert row["updated_at"] != "rev-current"
    await storage.close()


@pytest.mark.asyncio
async def test_stale_revision_does_not_change_metadata(tmp_path) -> None:
    """revision 不匹配时 canonical 行保持原样。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-stale.db"))
    await _create_document(storage)
    retriever = VectorRetriever(SimpleNamespace(document_storage=storage))

    assert (
        await retriever.update_metadata(
            17,
            {"importance": 0.9},
            expected_revision="rev-stale",
        )
        is False
    )

    async with storage.get_session() as session:
        row = (
            (
                await session.execute(
                    text("SELECT metadata, updated_at FROM documents WHERE id = 17")
                )
            )
            .mappings()
            .one()
        )
    assert json.loads(row["metadata"]) == {"importance": 0.5}
    assert row["updated_at"] == "rev-current"
    await storage.close()


@pytest.mark.asyncio
async def test_operational_metadata_cas_preserves_revision(tmp_path) -> None:
    """运行态 CAS 更新通过校验后仍保留原 source revision。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-operational-cas.db"))
    await _create_document(storage)
    retriever = VectorRetriever(SimpleNamespace(document_storage=storage))

    assert (
        await retriever.update_metadata(
            17,
            {"access_count": 2},
            expected_revision="rev-current",
            advance_revision=False,
        )
        is True
    )

    async with storage.get_session() as session:
        row = (
            (
                await session.execute(
                    text("SELECT metadata, updated_at FROM documents WHERE id = 17")
                )
            )
            .mappings()
            .one()
        )
    assert json.loads(row["metadata"])["access_count"] == 2
    assert row["updated_at"] == "rev-current"
    await storage.close()


@pytest.mark.asyncio
async def test_same_revision_content_update_keeps_canonical_id(tmp_path) -> None:
    """正文 CAS 更新保留 canonical ID，并拒绝旧 revision 重放。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-content-cas.db"))
    await _create_document(storage)
    vectors = _EmbeddingStorage()
    provider = SimpleNamespace(get_embedding=AsyncMock(return_value=[0.1, 0.2, 0.3]))
    db = SimpleNamespace(
        document_storage=storage,
        embedding_provider=provider,
        embedding_storage=vectors,
    )
    retriever = VectorRetriever(db)

    assert (
        await retriever.update_content_if_revision(
            17,
            "更新后的正文",
            {"importance": 0.9},
            "rev-current",
        )
        is True
    )
    assert vectors.deleted == [[17]]
    assert vectors.inserted == [17]

    async with storage.get_session() as session:
        row = (
            (
                await session.execute(
                    text("SELECT id, text, metadata FROM documents WHERE id = 17")
                )
            )
            .mappings()
            .one()
        )
    assert row["id"] == 17
    assert row["text"] == "更新后的正文"
    assert json.loads(row["metadata"])["importance"] == 0.9
    await storage.close()


async def _create_maintenance_document(storage: _DocumentStorage) -> None:
    """写入带 scope/privacy 的 canonical 行，供运行态维护行为测试使用。"""

    async with storage.engine.begin() as connection:
        await connection.execute(
            text(
                """CREATE TABLE documents (
                       id INTEGER PRIMARY KEY,
                       text TEXT NOT NULL,
                       metadata TEXT,
                       created_at TEXT,
                       updated_at TEXT
                   )"""
            )
        )
        await connection.execute(
            text(
                """INSERT INTO documents
                   (id,text,metadata,created_at,updated_at)
                   VALUES (17,'用户喜欢咖啡。',:metadata,'rev-created','rev-current')"""
            ),
            {
                "metadata": json.dumps(
                    {
                        "importance": 0.5,
                        "session_id": "session-1",
                        "privacy_level": "shared",
                    },
                    ensure_ascii=False,
                )
            },
        )


def _maintenance_engine(storage: _DocumentStorage) -> MemoryEngine:
    """构造覆盖 canonical 运行态维护写入的真实引擎边界。"""

    vector_retriever = VectorRetriever(SimpleNamespace(document_storage=storage))
    engine = MemoryEngine(
        db_path=":memory:",
        faiss_db=SimpleNamespace(document_storage=storage),
    )
    engine.hybrid_retriever = HybridRetriever(
        bm25_retriever=SimpleNamespace(),
        vector_retriever=vector_retriever,
        rrf_fusion=RRFFusion(),
    )
    return engine


async def _stored_maintenance_row(storage: _DocumentStorage) -> dict[str, Any]:
    """读取维护后的 canonical metadata 与 revision。"""

    async with storage.get_session() as session:
        return dict(
            (
                await session.execute(
                    text("SELECT metadata, updated_at FROM documents WHERE id = 17")
                )
            )
            .mappings()
            .one()
        )


@pytest.mark.asyncio
async def test_recall_reinforcement_keeps_revision_and_source_binding(tmp_path) -> None:
    """测试效应强化只写运行态字段：revision 与既有来源绑定保持不变。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-reinforce.db"))
    await _create_maintenance_document(storage)
    engine = _maintenance_engine(storage)

    assert await engine.reinforce_recall_state(17) is True

    row = await _stored_maintenance_row(storage)
    stored = json.loads(row["metadata"])
    # min(2x, 1.05^1) * 30 = 31.5，revision 列保持写入时的值。
    assert stored["reinforcement_count"] == 1
    assert stored["ttl_days"] == 31.5
    assert stored["importance"] == 0.5
    assert row["updated_at"] == "rev-current"

    # 强化后真实 Atom 来源校验仍通过：派生对象不会因运行态维护失效。
    memory = await engine.get_memory(17)
    assert memory is not None
    bound = bind_atoms_to_canonical_source([_bound_atom()], memory)
    validate_bound_atoms_match_canonical_source(bound, memory)
    await storage.close()


@pytest.mark.asyncio
async def test_interference_decay_keeps_revision(tmp_path) -> None:
    """自动干扰只衰减运行态重要性：revision 与 scope/privacy 均不改变。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-interference.db"))
    await _create_maintenance_document(storage)
    engine = _maintenance_engine(storage)

    assert await engine.apply_interference_decay(17, source_memory_id=99) is True

    row = await _stored_maintenance_row(storage)
    stored = json.loads(row["metadata"])
    assert stored["importance"] == 0.45
    assert stored["revised_by"] == 99
    assert stored["privacy_level"] == "shared"
    assert row["updated_at"] == "rev-current"
    await storage.close()


@pytest.mark.asyncio
async def test_runtime_maintenance_rejects_semantic_field_delta(tmp_path) -> None:
    """维护入口只接受白名单字段：越界增量被拒绝且不写 canonical。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-maintenance-guard.db"))
    await _create_maintenance_document(storage)
    engine = _maintenance_engine(storage)

    assert (
        await engine._persist_runtime_maintenance(  # noqa: SLF001
            17,
            lambda _metadata: {"privacy_level": "public"},
        )
        is False
    )

    row = await _stored_maintenance_row(storage)
    assert json.loads(row["metadata"])["privacy_level"] == "shared"
    assert row["updated_at"] == "rev-current"
    await storage.close()


@pytest.mark.asyncio
async def test_runtime_maintenance_rejects_concurrent_semantic_change(tmp_path) -> None:
    """读后写前发生语义更新时放弃维护，而不是覆盖新 revision。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-maintenance-cas.db"))
    await _create_maintenance_document(storage)
    engine = _maintenance_engine(storage)
    original_get_memory = engine.get_memory

    async def _competing_get_memory(memory_id: int):
        memory = await original_get_memory(memory_id)
        # 模拟并发人工编辑：读与写之间推进 canonical revision。
        await engine.hybrid_retriever.update_metadata(memory_id, {"importance": 0.9})
        return memory

    engine.get_memory = _competing_get_memory  # type: ignore[method-assign]

    assert await engine.reinforce_recall_state(17) is False
    assert engine.get_last_write_reason_code() == "source_revision_mismatch"
    row = await _stored_maintenance_row(storage)
    stored = json.loads(row["metadata"])
    assert stored["importance"] == 0.9
    assert "reinforcement_count" not in stored
    await storage.close()


@pytest.mark.asyncio
async def test_generic_metadata_update_commits_with_entry_revision(tmp_path) -> None:
    """未显式给出 revision 的通用 metadata 更新也必须走 CAS 并成功提交。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-generic-metadata.db"))
    await _create_maintenance_document(storage)
    engine = _maintenance_engine(storage)

    assert (
        await engine.update_memory(17, {"metadata": {"jargon_note": "已确认"}}) is True
    )

    row = await _stored_maintenance_row(storage)
    stored = json.loads(row["metadata"])
    assert stored["jargon_note"] == "已确认"
    await storage.close()


@pytest.mark.asyncio
async def test_generic_metadata_update_rejects_concurrent_write(tmp_path) -> None:
    """读后写前发生并发更新时拒绝覆盖，并暴露 source_revision_mismatch。"""

    storage = _DocumentStorage(str(tmp_path / "canonical-generic-metadata-cas.db"))
    await _create_maintenance_document(storage)
    engine = _maintenance_engine(storage)
    original_get_memory = engine.get_memory

    async def _competing_get_memory(memory_id: int):
        memory = await original_get_memory(memory_id)
        # 模拟并发写入：读与写之间推进 canonical revision。
        await engine.hybrid_retriever.update_metadata(memory_id, {"importance": 0.9})
        return memory

    engine.get_memory = _competing_get_memory  # type: ignore[method-assign]

    assert (
        await engine.update_memory(17, {"metadata": {"jargon_note": "unchecked"}})
        is False
    )
    assert engine.get_last_write_reason_code() == "source_revision_mismatch"
    row = await _stored_maintenance_row(storage)
    stored = json.loads(row["metadata"])
    assert "jargon_note" not in stored
    assert stored["importance"] == 0.9
    await storage.close()


def _bound_atom() -> MemoryAtom:
    """构造仍绑定写入时 revision 的用户事实 Atom。"""

    return MemoryAtom(
        parent_memory_id=17,
        parent_revision="rev-current",
        parent_scope_key="session-1",
        parent_privacy_level="shared",
        session_id="session-1",
        atom_type=AtomType.FACTUAL,
        content="用户喜欢咖啡。",
        importance=0.5,
        confidence=0.9,
        source_evidence=source_evidence("用户喜欢咖啡。"),
    )
