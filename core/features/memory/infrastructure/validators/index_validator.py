"""
索引一致性验证器 - 检测并修复索引与数据库的不一致问题
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, cast

import aiosqlite
from astrbot.api import logger

from ..base import apply_perf_pragmas
from ..sql_contract import (
    MEMORY_FTS_CLEAR_SQL,
    MEMORY_FTS_COUNT_DISTINCT_DOC_IDS_SQL,
    MEMORY_FTS_SELECT_DISTINCT_DOC_IDS_SQL,
    MEMORY_FTS_TABLE,
)
from .index_rebuilder import IndexRebuilderMixin


@dataclass
class IndexStatus:
    """索引状态信息"""

    is_consistent: bool  # 是否一致
    documents_count: int  # documents表中的文档数
    bm25_count: int  # BM25索引中的文档数
    vector_count: int  # 向量索引中的文档数
    missing_in_bm25: int  # documents中有但BM25中缺失的数量
    missing_in_vector: int  # documents中有但向量索引中缺失的数量
    needs_rebuild: bool  # 是否需要重建
    reason: str  # 不一致的原因描述


class IndexValidator(IndexRebuilderMixin):
    """
    索引一致性验证器

    检测documents表与BM25索引、向量索引之间的一致性
    """

    def __init__(self, db_path: str, faiss_db: Any):
        """
        初始化验证器

        Args:
            db_path: SQLite数据库路径
            faiss_db: FaissVecDB实例
        """
        self.db_path = db_path
        self.faiss_db = faiss_db

    DEFAULT_REBUILD_BATCH_SIZE = 50
    DEFAULT_EMBEDDING_BATCH_SIZE = 8
    DEFAULT_TASKS_LIMIT = 1
    DEFAULT_MAX_RETRIES = 5
    DEFAULT_RETRY_BASE_DELAY = 30.0
    DEFAULT_BATCH_DELAY = 5.0
    DEFAULT_REQUEST_DELAY = 5.0
    RATE_LIMIT_RETRY_MIN_DELAY = 30.0
    DEFAULT_MAX_FAILURE_RATIO = 0.02
    _ALLOWED_FTS_TABLES = frozenset({MEMORY_FTS_TABLE})

    @classmethod
    def _validate_fts_table_name(cls, table_name: str) -> str:
        normalized = str(table_name or "").strip()
        if normalized not in cls._ALLOWED_FTS_TABLES:
            raise ValueError(f"unsupported FTS table: {normalized}")
        return normalized

    async def _clear_bm25_with_retry(
        self, table_name: str = MEMORY_FTS_TABLE, max_attempts: int = 5
    ) -> None:
        """清空 BM25 索引表，不触碰 documents 原始数据。"""
        self._validate_fts_table_name(table_name)
        for attempt in range(max_attempts):
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await apply_perf_pragmas(db)
                    try:
                        await db.execute(MEMORY_FTS_CLEAR_SQL)
                    except Exception as e:
                        logger.warning(f"清空BM25索引失败: {e}")
                    await db.commit()
                return
            except Exception as e:
                if (
                    "database is locked" in str(e).lower()
                    and attempt < max_attempts - 1
                ):
                    wait_seconds = 0.2 * (attempt + 1)
                    logger.warning(
                        f"清空SQLite存储遇到锁，{wait_seconds:.1f}s后重试 "
                        f"({attempt + 1}/{max_attempts}): {e}"
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                raise

    async def check_consistency(self) -> IndexStatus:
        """
        检查索引一致性

        Returns:
            IndexStatus: 索引状态信息
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await apply_perf_pragmas(db)
                # 1. 获取documents表中的文档数和ID集合
                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                count_result = await cursor.fetchone()
                documents_count = count_result[0] if count_result else 0

                cursor = await db.execute("SELECT id FROM documents")
                doc_ids = {row[0] for row in await cursor.fetchall()}

                # 2. 检查BM25索引（memora_memories_fts表）
                cursor = await db.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = :table_name
                    """,
                    {"table_name": MEMORY_FTS_TABLE},
                )
                has_fts_table = await cursor.fetchone()

                if has_fts_table:
                    cursor = await db.execute(MEMORY_FTS_COUNT_DISTINCT_DOC_IDS_SQL)
                    bm25_result = await cursor.fetchone()
                    bm25_count = bm25_result[0] if bm25_result else 0

                    cursor = await db.execute(MEMORY_FTS_SELECT_DISTINCT_DOC_IDS_SQL)
                    bm25_ids = {row[0] for row in await cursor.fetchall()}
                else:
                    bm25_count = 0
                    bm25_ids = set()

                # 3. 检查向量索引
                vector_count = 0
                vector_ids = set()

                try:
                    embedding_storage = getattr(
                        self.faiss_db, "embedding_storage", None
                    )
                    index = getattr(embedding_storage, "index", None)
                    if index is not None:
                        vector_count = int(getattr(index, "ntotal", 0))
                        # Try to get concrete vector IDs from IndexIDMap.
                        try:
                            import faiss

                            if hasattr(index, "id_map"):
                                vector_to_array = getattr(
                                    faiss, "vector_to_array", None
                                )
                                if callable(vector_to_array):
                                    raw_ids = cast(Any, vector_to_array(index.id_map))
                                    vector_ids = {int(i) for i in raw_ids}
                        except Exception as e:
                            logger.debug(f"读取向量ID失败，使用计数模式: {e}")
                except Exception as e:
                    logger.warning(f"检查向量索引失败: {e}")

                # 4. 计算差异
                missing_in_bm25 = len(doc_ids - bm25_ids)
                if vector_ids:
                    missing_in_vector = len(doc_ids - vector_ids)
                else:
                    missing_in_vector = max(0, documents_count - vector_count)

                # 5. 判断是否需要重建
                needs_rebuild = False
                reason = ""

                if documents_count == 0:
                    reason = "数据库为空"
                    is_consistent = True
                elif missing_in_bm25 > 0 or missing_in_vector > 0:
                    needs_rebuild = True
                    is_consistent = False
                    reasons = []
                    if missing_in_bm25 > 0:
                        reasons.append(f"BM25索引缺失{missing_in_bm25}条文档")
                    if missing_in_vector > 0:
                        reasons.append(f"向量索引缺失{missing_in_vector}条文档")
                    reason = "；".join(reasons)
                elif bm25_count > documents_count:
                    needs_rebuild = True
                    is_consistent = False
                    reason = "BM25索引中存在冗余数据"
                elif vector_count > documents_count:
                    # FAISS ntotal 包含逻辑删除的槽位，冗余向量不影响召回正确性，
                    # 不触发全量重建（否则每次启动都会重建）
                    is_consistent = True
                    reason = f"向量索引含{vector_count - documents_count}条冗余槽位（正常，不影响召回）"
                else:
                    is_consistent = True
                    reason = "索引状态正常"

                return IndexStatus(
                    is_consistent=is_consistent,
                    documents_count=documents_count,
                    bm25_count=bm25_count,
                    vector_count=vector_count,
                    missing_in_bm25=missing_in_bm25,
                    missing_in_vector=missing_in_vector,
                    needs_rebuild=needs_rebuild,
                    reason=reason,
                )

        except Exception as e:
            logger.error(f"检查索引一致性失败: {e}", exc_info=True)
            return IndexStatus(
                is_consistent=False,
                documents_count=0,
                bm25_count=0,
                vector_count=0,
                missing_in_bm25=0,
                missing_in_vector=0,
                needs_rebuild=True,
                reason=f"检查失败: {str(e)}",
            )

    def _get_rebuild_options(self, memory_engine: Any) -> dict[str, Any]:
        config = getattr(memory_engine, "config", {}) or {}

        def read_int(key: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        def read_float(
            key: str, default: float, minimum: float, maximum: float
        ) -> float:
            try:
                value = float(config.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        return {
            "batch_size": read_int(
                "index_rebuild_batch_size", self.DEFAULT_REBUILD_BATCH_SIZE, 1, 500
            ),
            "embedding_batch_size": read_int(
                "index_rebuild_embedding_batch_size",
                self.DEFAULT_EMBEDDING_BATCH_SIZE,
                1,
                256,
            ),
            "tasks_limit": read_int(
                "index_rebuild_tasks_limit", self.DEFAULT_TASKS_LIMIT, 1, 8
            ),
            "max_retries": read_int(
                "index_rebuild_max_retries", self.DEFAULT_MAX_RETRIES, 1, 8
            ),
            "retry_base_delay": read_float(
                "index_rebuild_retry_base_delay",
                self.DEFAULT_RETRY_BASE_DELAY,
                0.0,
                60.0,
            ),
            "batch_delay": read_float(
                "index_rebuild_batch_delay", self.DEFAULT_BATCH_DELAY, 0.0, 10.0
            ),
            "request_delay": read_float(
                "index_rebuild_request_delay", self.DEFAULT_REQUEST_DELAY, 0.0, 60.0
            ),
            "max_failure_ratio": read_float(
                "index_rebuild_max_failure_ratio",
                self.DEFAULT_MAX_FAILURE_RATIO,
                0.0,
                1.0,
            ),
        }

    @staticmethod
    def _failure_ratio(errors: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return errors / total

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "429" in message
            or "rate limit" in message
            or "tpm limit" in message
            or "too many requests" in message
        )

    async def _get_document_count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            await apply_perf_pragmas(db)
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def _get_document_ids(self) -> set[int]:
        async with aiosqlite.connect(self.db_path) as db:
            await apply_perf_pragmas(db)
            cursor = await db.execute("SELECT id FROM documents")
            return {int(row[0]) for row in await cursor.fetchall()}

    async def _iter_document_batches(
        self,
        batch_size: int,
        document_ids: set[int] | None = None,
    ):
        if document_ids is not None:
            sorted_ids = sorted(int(doc_id) for doc_id in document_ids)
            for start in range(0, len(sorted_ids), batch_size):
                chunk = sorted_ids[start : start + batch_size]
                async with aiosqlite.connect(self.db_path) as db:
                    await apply_perf_pragmas(db)
                    cursor = await db.execute(
                        """
                        SELECT id, doc_id, text, metadata
                        FROM documents
                        WHERE id IN (SELECT value FROM json_each(:ids_json))
                        ORDER BY id
                        """,
                        {"ids_json": json.dumps(chunk)},
                    )
                    yield await cursor.fetchall()
            return

        last_id = 0
        while True:
            async with aiosqlite.connect(self.db_path) as db:
                await apply_perf_pragmas(db)
                cursor = await db.execute(
                    """
                    SELECT id, doc_id, text, metadata
                    FROM documents
                    WHERE id > ?
                    ORDER BY id
                    LIMIT ?
                    """,
                    (last_id, batch_size),
                )
                rows = await cursor.fetchall()

            if not rows:
                break
            last_id = int(rows[-1][0])
            yield rows

    def _get_vector_count(self) -> int:
        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        index = getattr(embedding_storage, "index", None)
        if index is None:
            return 0
        return int(getattr(index, "ntotal", 0))

    def _get_vector_ids(self) -> set[int] | None:
        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        index = getattr(embedding_storage, "index", None)
        if index is None:
            return set()
        try:
            import faiss

            if hasattr(index, "id_map"):
                vector_to_array = getattr(faiss, "vector_to_array", None)
                if callable(vector_to_array):
                    raw_ids = cast(Any, vector_to_array(index.id_map))
                    return {int(i) for i in raw_ids}
        except Exception as e:
            logger.debug(f"读取向量ID失败: {e}")
        return None
