"""
BM25检索器 - 基于SQLite FTS5的稀疏检索
实现简洁的BM25检索功能,用于MemoryEngine的混合检索
"""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aiosqlite

from astrbot.api import logger

from ..adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    NormalizationScope,
    ScoreDirection,
    ScoreSemantics,
)
from ..processors.text_processor import TextProcessor
from ..storage.base import apply_perf_pragmas


@dataclass
class BM25Result:
    """BM25检索结果"""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]


class BM25Retriever:
    """
    文档路 BM25 关键词检索器

    使用SQLite FTS5实现BM25算法的全文检索。
    主要特性:
    1. 使用TextProcessor进行中文分词和停用词过滤
    2. 支持通过metadata过滤session_id和persona_id
    3. BM25分数自动归一化到[0,1]区间
    """

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.LEXICAL_RETRIEVER,
        native=frozenset(
            {
                AdapterCapability.SCORING,
                AdapterCapability.UPDATE,
                AdapterCapability.DELETE,
            }
        ),
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.CANCELLATION,
                AdapterCapability.REFERENCE_TIME,
            }
        ),
        score=ScoreSemantics(
            direction=ScoreDirection.HIGHER_IS_BETTER,
            minimum=0.0,
            maximum=1.0,
            normalization=NormalizationScope.PER_QUERY,
        ),
    )

    _ALLOWED_FTS_TABLES = frozenset({"memora_memories_fts"})
    _ALLOWED_DOC_TABLES = frozenset({"documents"})

    def __init__(
        self,
        db_path: str,
        text_processor: TextProcessor,
        config: dict[str, Any] | None = None,
    ):
        """
        初始化BM25检索器

        Args:
            db_path: SQLite数据库路径
            text_processor: 文本处理器实例
            config: 配置字典(可选)
        """
        self.db_path = db_path
        self.text_processor = text_processor
        self.config = config or {}
        self.fts_table = "memora_memories_fts"
        self.doc_table = "documents"

    @classmethod
    def _quote_identifier(
        cls,
        identifier: str,
        *,
        allowed: frozenset[str],
        label: str,
    ) -> str:
        """验证标识符是否在允许列表中，并加上 SQL 双引号。"""
        if identifier not in allowed:
            raise ValueError(f"Unsupported {label}: {identifier!r}")
        return f'"{identifier}"'

    @property
    def _fts_table_sql(self) -> str:
        return self._quote_identifier(
            self.fts_table,
            allowed=self._ALLOWED_FTS_TABLES,
            label="FTS table",
        )

    @property
    def _doc_table_sql(self) -> str:
        return self._quote_identifier(
            self.doc_table,
            allowed=self._ALLOWED_DOC_TABLES,
            label="document table",
        )

    @asynccontextmanager
    async def _connect(self):
        """创建新的SQLite连接并应用统一 PRAGMA。"""
        db = await aiosqlite.connect(self.db_path)
        try:
            await apply_perf_pragmas(db)
            yield db
        finally:
            await db.close()

    async def initialize(self):
        """
        初始化FTS5索引

        创建 memora_memories_fts 虚拟表用于全文检索。
        使用unicode61分词器处理已预处理的文本。
        """
        async with self._connect() as db:
            await self._warn_if_legacy_documents_fts_exists(db)
            fts_table_sql = self._fts_table_sql
            # 创建FTS5虚拟表
            await db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {fts_table_sql}
                USING fts5(
                    content,
                    doc_id UNINDEXED,
                    tokenize='unicode61'
                )
            """)
            await db.commit()

    async def _warn_if_legacy_documents_fts_exists(self, db: aiosqlite.Connection):
        cursor = await db.execute("""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='documents_fts'
        """)
        row = await cursor.fetchone()
        if not row:
            return

        if await self._is_legacy_memora_documents_fts(db, row[0] or ""):
            logger.warning(
                "检测到插件废弃 documents_fts 表；当前版本改用 memora_memories_fts，"
                "请确认数据库迁移已执行到 v6"
            )

    @staticmethod
    async def _is_legacy_memora_documents_fts(
            db: aiosqlite.Connection,
        create_sql: str,
    ) -> bool:
        normalized_sql = " ".join(create_sql.lower().replace("\n", " ").split())
        expected_sql = "create virtual table documents_fts using fts5(content, doc_id, tokenize='unicode61')"
        if normalized_sql != expected_sql:
            return False

        cursor = await db.execute("PRAGMA table_xinfo(documents_fts)")
        rows = await cursor.fetchall()
        visible_columns = [row[1] for row in rows if int(row[6]) == 0]
        return visible_columns == ["content", "doc_id"]

    async def add_document(
        self, doc_id: int, content: str, metadata: dict[str, Any] | None = None
    ):
        """
        添加文档到BM25索引

        Args:
            doc_id: 文档ID
            content: 文档内容(原始文本)
            metadata: 文档元数据(可选,用于过滤但不索引)
        """
        # 使用TextProcessor预处理文本（异步卸载 jieba 分词到线程池）
        tokens = await self.text_processor.tokenize_async(
            content, remove_stopwords=True
        )
        processed_content = " ".join(tokens)

        async with self._connect() as db:
            fts_table_sql = self._fts_table_sql
            # 插入到FTS表
            await db.execute(
                f"INSERT INTO {fts_table_sql}(doc_id, content) VALUES (?, ?)",
                (doc_id, processed_content),
            )
            await db.commit()

    async def search(
        self,
        query: str,
        limit: int = 50,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[BM25Result]:
        """
        执行BM25搜索

        Args:
            query: 查询字符串
            limit: 返回结果数量
            session_id: 会话ID过滤(可选)
            persona_id: 人格ID过滤(可选)

        Returns:
            BM25Result列表,按归一化分数降序排列
        """
        if not query or not query.strip():
            return []

        # 预处理查询（异步卸载 jieba 分词到线程池）
        tokens = await self.text_processor.tokenize_async(query, remove_stopwords=True)
        if not tokens:
            return []

        # 构建FTS5查询: 使用OR连接多个token,提高召回率
        # 转义特殊字符
        escaped_tokens = []
        for token in tokens:
            # 转义FTS5特殊字符
            escaped = token.replace('"', '""')
            escaped_tokens.append(f'"{escaped}"')

        # 使用OR连接所有token
        fts_query = " OR ".join(escaped_tokens)

        # 有过滤条件时大幅增加预取量，避免过滤后结果不足
        # Python 层过滤（BM25）比 FAISS 内部过滤损耗更大，需要更多候选
        has_filters = session_id is not None or persona_id is not None
        fetch_limit = limit * 10 if has_filters else limit * 2

        async with self._connect() as db:
            fts_table_sql = self._fts_table_sql
            doc_table_sql = self._doc_table_sql
            # 执行FTS5 BM25搜索
            # 注意: SQLite FTS5 bm25() 分数越小越相关（常见为负数）
            cursor = await db.execute(
                f"""
                SELECT doc_id, bm25({fts_table_sql}) as score
                FROM {fts_table_sql}
                WHERE {fts_table_sql} MATCH ?
                ORDER BY score ASC
                LIMIT ?
            """,
                (fts_query, fetch_limit),
            )  # 多取一些以备过滤后不足

            fts_results = await cursor.fetchall()

            if not fts_results:
                return []

            # 获取文档详情
            doc_ids = [row[0] for row in fts_results]
            placeholders = ",".join("?" * len(doc_ids))

            cursor = await db.execute(
                f"""
                SELECT id, text, metadata
                FROM {doc_table_sql}
                WHERE id IN ({placeholders})
            """,
                doc_ids,
            )

            docs = {}
            async for row in cursor:
                doc_id, text, metadata_json = row
                metadata = json.loads(metadata_json) if metadata_json else {}
                docs[doc_id] = {"text": text, "metadata": metadata}

            # 构建结果列表并应用过滤
            results = []
            for doc_id, bm25_score in fts_results:
                if doc_id not in docs:
                    continue

                doc = docs[doc_id]
                metadata = doc["metadata"]

                # 应用过滤器 - 直接比较完整的 session_id / persona_id
                if session_id is not None:
                    stored_session_id = metadata.get("session_id")
                    if stored_session_id != session_id:
                        continue
                if persona_id is not None:
                    stored_persona_id = metadata.get("persona_id")
                    if stored_persona_id != persona_id:
                        continue

                results.append(
                    BM25Result(
                        doc_id=doc_id,
                        score=bm25_score,
                        content=doc["text"],
                        metadata=metadata,
                    )
                )

                # 达到limit后停止
                if len(results) >= limit:
                    break

            # 归一化分数到[0, 1]
            if results:
                # FTS5 bm25 分数越小越相关，归一化后分数越大越相关
                scores = [r.score for r in results]
                max_score = max(scores)
                min_score = min(scores)

                if max_score == min_score:
                    for result in results:
                        result.score = 1.0
                else:
                    score_range = max_score - min_score
                    for result in results:
                        # 归一化: (max - score) / range
                        result.score = (max_score - result.score) / score_range

            return results

    async def delete_document(self, doc_id: int) -> bool:
        """
        从BM25索引删除文档

        Args:
            doc_id: 文档ID

        Returns:
            bool: 是否成功删除
        """
        from astrbot.api import logger

        try:
            async with self._connect() as db:
                fts_table_sql = self._fts_table_sql
                await db.execute(
                    f"DELETE FROM {fts_table_sql} WHERE doc_id = ?", (doc_id,)
                )
                await db.commit()
                return True

        except Exception as exc:
            logger.error(
                "[BM25 删除] 失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False

    async def update_document(
        self, doc_id: int, content: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """
        更新BM25索引中的文档（重新索引）

        Args:
            doc_id: 文档ID
            content: 新内容
            metadata: 新元数据（当前仅用于日志）

        Returns:
            bool: 是否成功更新
        """
        from astrbot.api import logger

        try:
            # 重新处理内容（异步卸载 jieba 分词到线程池）
            tokens = await self.text_processor.tokenize_async(
                content, remove_stopwords=True
            )
            processed_content = " ".join(tokens)

            async with self._connect() as db:
                fts_table_sql = self._fts_table_sql
                # 先删除旧索引
                await db.execute(
                    f"DELETE FROM {fts_table_sql} WHERE doc_id = ?", (doc_id,)
                )

                # 插入新索引
                await db.execute(
                    f"INSERT INTO {fts_table_sql}(doc_id, content) VALUES (?, ?)",
                    (doc_id, processed_content),
                )

                await db.commit()
                logger.debug("[BM25] 成功更新文档索引")
                return True

        except Exception as exc:
            logger.error(
                "[BM25] 更新文档失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False
