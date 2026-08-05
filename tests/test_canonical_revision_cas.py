"""验证 canonical metadata 更新使用 SQLite 原子 revision 比较。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.retrieval.vector_retriever import VectorRetriever


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
