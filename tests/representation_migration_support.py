"""canonical 表示迁移测试的共享 fixture：临时 SQLite、canonical 行与引擎边界。

只被本主题的测试文件导入；不连接真实实例、不读取生产数据。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import aiosqlite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.features.memory.application.canonical_representation_migration import (
    TARGET_REPRESENTATION_VERSION,
)
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.retrieval.hybrid_retriever import HybridRetriever
from core.features.retrieval.rrf_fusion import RRFFusion
from core.features.retrieval.vector_retriever import VectorRetriever


def _legacy_metadata() -> dict[str, Any]:
    """历史格式：正文是叙述，事实与摘要都在 metadata。"""

    return {
        "importance": 0.6,
        "session_id": "session-anon",
        "privacy_level": "confidential",
        "key_facts": ["喜欢咖啡", "在上海工作"],
        "summary": "用户喜欢咖啡，并在上海工作。",
    }


def _legacy_content() -> str:
    return "用户喜欢咖啡，并在上海工作。"


class _DocumentStorage:
    """为真实 CAS 写入提供最小 SQLAlchemy 会话边界。"""

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
        """按 ID 读取最小 canonical 记录，供引擎详情与 CAS 写回使用。"""

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
    """记录正文 CAS 后的派生向量替换。"""

    dimension = 3

    def __init__(self) -> None:
        """初始化操作记录。"""

        self.deleted: list[list[int]] = []
        self.inserted: list[int] = []

    async def delete(self, ids: list[int]) -> None:
        """记录删除旧向量。"""

        self.deleted.append(ids)

    async def insert(self, vector: Any, doc_id: int) -> None:
        """记录插入新向量。"""

        self.inserted.append(doc_id)


class _InterleavingConnection:
    """在页内 revision 复核前触发一次并发写入。"""

    def __init__(
        self, connection: Any, *, db_path: str, target_id: int, revision: str
    ) -> None:
        """保存底层连接与并发写入参数。"""

        self._connection = connection
        self._db_path = db_path
        self._target_id = target_id
        self._revision = revision
        self._fired = False

    async def execute(self, sql: str, parameters: Any = ()) -> Any:
        """首次复核页内 revision 前，用独立连接提交一次并发更新。"""

        if not self._fired and "WHERE id >= ? AND id <= ?" in sql:
            self._fired = True
            async with aiosqlite.connect(self._db_path) as writer:
                await writer.execute(
                    "UPDATE documents SET updated_at = ? WHERE id = ?",
                    (self._revision, self._target_id),
                )
                await writer.commit()
        return await self._connection.execute(sql, parameters)

    async def commit(self) -> None:
        """转发提交（dry-run 不会调用，apply 复用时保持可用）。"""

        await self._connection.commit()


def _legacy_row(memory_id: int, *, updated_at: str) -> tuple[Any, ...]:
    """历史格式 canonical 行：正文为叙述，无 canonical_summary。"""

    return (
        memory_id,
        _legacy_content(),
        _legacy_metadata(),
        f"{updated_at}-created",
        updated_at,
    )


def _current_row(memory_id: int, *, updated_at: str) -> tuple[Any, ...]:
    """当前格式 canonical 行：正文为事实连接文本。"""

    content = "喜欢咖啡；在上海工作"
    return (
        memory_id,
        content,
        {
            "importance": 0.6,
            "session_id": "session-anon",
            "privacy_level": "confidential",
            "key_facts": ["喜欢咖啡", "在上海工作"],
            "fact_source_evidence": [
                [{"role": "user", "message_id": "m1"}],
                [{"role": "user", "message_id": "m2"}],
            ],
            "canonical_summary": content,
            "persona_summary": _legacy_content(),
            "summary_schema_version": TARGET_REPRESENTATION_VERSION,
        },
        f"{updated_at}-created",
        updated_at,
    )


async def _create_schema(db_path: str, *, migration_status: bool = True) -> None:
    """创建最小 canonical documents 表与可选 migration_status 表。"""

    storage = _DocumentStorage(db_path)
    try:
        async with storage.engine.begin() as connection:
            await connection.execute(
                text(
                    """CREATE TABLE documents (
                           id INTEGER PRIMARY KEY,
                           doc_id TEXT,
                           text TEXT NOT NULL,
                           metadata TEXT DEFAULT '{}',
                           created_at TEXT,
                           updated_at TEXT
                       )"""
                )
            )
            if migration_status:
                await connection.execute(
                    text(
                        """CREATE TABLE migration_status (
                               key TEXT PRIMARY KEY,
                               value TEXT,
                               updated_at TEXT
                           )"""
                    )
                )
    finally:
        await storage.close()


async def _seed(db_path: str, rows: list[tuple[Any, ...]]) -> None:
    """写入 canonical 行。"""

    storage = _DocumentStorage(db_path)
    try:
        async with storage.engine.begin() as connection:
            for memory_id, content, metadata, created_at, updated_at in rows:
                await connection.execute(
                    text(
                        """INSERT INTO documents
                           (id, text, metadata, created_at, updated_at)
                           VALUES (:id, :text, :metadata, :created_at, :updated_at)"""
                    ),
                    {
                        "id": memory_id,
                        "text": content,
                        "metadata": json.dumps(metadata, ensure_ascii=False),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )
    finally:
        await storage.close()


async def _read_row(db_path: str, memory_id: int) -> dict[str, Any] | None:
    """读取 canonical 行快照。"""

    async with aiosqlite.connect(db_path) as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            "SELECT id, text, metadata, created_at, updated_at FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return dict(row) if row is not None else None


async def _read_checkpoint(db_path: str) -> dict[str, Any] | None:
    """读取唯一的迁移 checkpoint 行。"""

    async with aiosqlite.connect(db_path) as connection:
        connection.row_factory = aiosqlite.Row
        cursor = await connection.execute(
            "SELECT key, value FROM migration_status WHERE key LIKE 'representation_migration:apply:v1:%'"
        )
        row = await cursor.fetchone()
        await cursor.close()
    if row is None:
        return None
    return json.loads(row["value"])


def _engine_with_real_cas(
    storage: _DocumentStorage, *, bm25_retriever: Any | None = None
) -> MemoryEngine:
    """构造覆盖真实 canonical CAS 写入的引擎边界。"""

    provider = SimpleNamespace(get_embedding=AsyncMock(return_value=[0.1, 0.2, 0.3]))
    faiss_db = SimpleNamespace(
        document_storage=storage,
        embedding_provider=provider,
        embedding_storage=_EmbeddingStorage(),
    )
    engine = MemoryEngine(db_path=":memory:", faiss_db=faiss_db)
    engine.hybrid_retriever = HybridRetriever(
        bm25_retriever=bm25_retriever
        or SimpleNamespace(update_document=AsyncMock(return_value=True)),
        vector_retriever=VectorRetriever(faiss_db),
        rrf_fusion=RRFFusion(),
    )
    return engine
