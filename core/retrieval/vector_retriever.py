"""
向量检索器，基于 Faiss 的向量密集检索。
封装 AstrBot 的 FaissVecDB，并提供统一的检索接口。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

from ..adapter_capabilities import (
    ASTRBOT_FAISS_CAPABILITIES,
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    ScoreDirection,
    ScoreSemantics,
    bind_default_adapter_contract,
)

_TRUNCATED_CONTENT_MARKER = "\n...[中间内容已截断]...\n"

@dataclass
class VectorResult:
    """向量检索结果"""

    doc_id: int
    score: float
    content: str
    metadata: dict[str, Any]


class VectorRetriever:
    """
    向量密集检索器

    封装 AstrBot 的 FaissVecDB，提供统一的向量相似度检索接口。
    主要特性:
    1. 保持查询文本原样检索，避免额外预处理带来的行为分叉
    2. 元数据包含：importance、create_time、last_access_time、session_id、persona_id
    3. 相似度分数按 higher-is-better 解释，静态范围未知
    4. 支持通过 metadata 过滤 session_id 和 persona_id
    5. 通过 ID 映射缓存优化 UUID 查询性能
    """

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.VECTOR_RETRIEVER,
        native=frozenset({AdapterCapability.SCORING}),
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.UPDATE,
                AdapterCapability.DELETE,
                AdapterCapability.CANCELLATION,
                AdapterCapability.REFERENCE_TIME,
            }
        ),
        score=ScoreSemantics(direction=ScoreDirection.HIGHER_IS_BETTER),
    )

    def __init__(
        self,
        faiss_db: FaissVecDB,
        config: dict[str, Any] | None = None,
    ):
        """
        初始化向量检索器

        参数:
            faiss_db: FaissVecDB 实例
            config: 配置字典（可选）
        """
        self.faiss_db = faiss_db
        self.backend_capabilities = bind_default_adapter_contract(
            faiss_db,
            ASTRBOT_FAISS_CAPABILITIES,
        )
        self.config = config or {}

        # 优化 3：ID 映射缓存（int_id -> uuid）
        self._id_cache: dict[int, str] = {}
        self._cache_max_size = self.config.get("recall_engine.id_cache_size", 1000)

    @staticmethod
    def _fit_content_for_embedding(content: str, max_chars: int) -> str:
        """在字符预算内同时保留开头上下文与结尾结论。"""
        if len(content) <= max_chars:
            return content

        if max_chars <= len(_TRUNCATED_CONTENT_MARKER):
            return content[:max_chars]

        available = max_chars - len(_TRUNCATED_CONTENT_MARKER)
        head_chars = available // 2
        tail_chars = available - head_chars
        return content[:head_chars] + _TRUNCATED_CONTENT_MARKER + content[-tail_chars:]

    async def add_document(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> int:
        """
        添加文档到向量库

        参数:
            content: 文档内容
            metadata: 文档元数据（必须包含：importance、create_time、
                last_access_time、session_id、persona_id）

        返回:
            文档 ID。
        """
        # 确保 metadata 存在
        metadata = metadata or {}

        # 验证必需的元数据字段
        required_fields = [
            "importance",
            "create_time",
            "last_access_time",
            "session_id",
            "persona_id",
        ]
        for field in required_fields:
            if field not in metadata:
                # 提供默认值
                if field == "importance":
                    metadata[field] = 0.5
                elif field in ["create_time", "last_access_time"]:
                    import time

                    metadata[field] = time.time()
                else:  # session_id、persona_id
                    metadata[field] = None

        # 插入到 Faiss 向量库，同时截断过长内容以防 embedding token 超限
        _MAX_CONTENT_CHARS = 4000  # noqa: N806
        insert_content = content
        if len(insert_content) > _MAX_CONTENT_CHARS:
            from astrbot.api import logger as _logger

            _logger.warning(
                f"[向量检索器] 记忆内容过长（{len(insert_content)} 字符），"
                f"保留开头和结尾并压缩至 {_MAX_CONTENT_CHARS} 字符"
            )
            insert_content = self._fit_content_for_embedding(
                insert_content,
                _MAX_CONTENT_CHARS,
            )
        doc_id = await self.faiss_db.insert(content=insert_content, metadata=metadata)

        return doc_id

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[VectorResult]:
        """
        执行向量相似度搜索

        参数:
            query: 查询字符串
            k: 返回的结果数量
            session_id: 会话 ID 过滤（可选）
            persona_id: 人格 ID 过滤（可选）

        返回:
            按相似度降序排列的向量检索结果列表。
        """
        if not query or not query.strip():
            return []

        processed_query = query

        # 防止 embedding API token 超限：截断过长的查询文本
        # 大多数 embedding 模型限制在 8192 tokens 以内，按字符数保守截断
        _MAX_QUERY_CHARS = 2000  # noqa: N806
        if len(processed_query) > _MAX_QUERY_CHARS:
            from astrbot.api import logger as _logger

            _logger.warning(
                f"[向量检索器] 查询文本过长（{len(processed_query)} 字符），"
                f"截断至 {_MAX_QUERY_CHARS} 字符以避免 token 超限"
            )
            processed_query = processed_query[:_MAX_QUERY_CHARS]

        # 构建元数据过滤器
        metadata_filters = {}
        if session_id is not None:
            metadata_filters["session_id"] = session_id
        if persona_id is not None:
            metadata_filters["persona_id"] = persona_id

        if metadata_filters and not self.backend_capabilities.supports(
            AdapterCapability.FILTERING
        ):
            return []

        # 执行向量检索
        # 将 fetch_k 设为 k*2，确保过滤后仍有足够结果
        fetch_k = k * 2 if metadata_filters else k

        faiss_results = await self.faiss_db.retrieve(
            query=processed_query,
            k=k,
            fetch_k=fetch_k,
            rerank=False,
            metadata_filters=metadata_filters if metadata_filters else None,
        )

        # 转换为 VectorResult 格式
        results = []
        for result in faiss_results:
            # FaissVecDB 返回的 Result 对象包含 similarity 和 data
            # 其中 data 是包含 id、text、metadata 的字典
            doc_data = result.data
            doc_metadata = doc_data.get("metadata")
            if metadata_filters and (
                not isinstance(doc_metadata, dict)
                or any(
                    doc_metadata.get(field) != expected
                    for field, expected in metadata_filters.items()
                )
            ):
                continue
            similarity = float(result.similarity)
            if not math.isfinite(similarity):
                continue
            results.append(
                VectorResult(
                    doc_id=doc_data["id"],
                    score=similarity,
                    content=doc_data["text"],
                    metadata=doc_metadata,
                )
            )

        return results

    async def _get_uuid_from_id(self, doc_id: int) -> str | None:
        """
        获取文档的 UUID（带缓存优化）

        参数:
            doc_id: 整数文档 ID

        返回:
            UUID 字符串；如果不存在则返回 None。
        """
        # 优化 3：优先查询缓存
        if doc_id in self._id_cache:
            return self._id_cache[doc_id]

        from astrbot.api import logger

        try:
            doc_storage = self.faiss_db.document_storage
            docs = await doc_storage.get_documents(
                metadata_filters={}, ids=[doc_id], limit=1
            )

            if not docs or len(docs) == 0:
                return None

            uuid_doc_id = docs[0].get("doc_id")

            # 更新缓存
            if uuid_doc_id and len(self._id_cache) < self._cache_max_size:
                self._id_cache[doc_id] = uuid_doc_id

            return uuid_doc_id

        except Exception as exc:
            logger.error(
                "[向量映射查询] 失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return None

    async def update_metadata(
        self,
        doc_id: int,
        metadata: dict[str, Any],
        expected_revision: str | None = None,
    ) -> bool:
        """
        更新文档元数据（使用 ORM 方式）

        参数:
            doc_id: 文档 ID（整数 id）
            metadata: 新的元数据字典
            expected_revision: 可选的 source revision；提供时使用 SQLite 原子比较更新

        返回:
            是否成功更新。
        """
        if not self.backend_capabilities.supports(AdapterCapability.UPDATE):
            return False

        if expected_revision is not None:
            return await self._update_metadata_if_revision(
                doc_id,
                metadata,
                expected_revision,
            )

        import json

        from astrbot.api import logger

        try:
            doc_storage = self.faiss_db.document_storage

            # 通过 id 获取文档
            docs = await doc_storage.get_documents(
                metadata_filters={}, ids=[doc_id], limit=1
            )

            if not docs or len(docs) == 0:
                logger.warning("[元数据更新] 文档不存在")
                return False

            doc = docs[0]

            # 获取当前元数据并更新
            current_metadata_str = doc.get("metadata", "{}")
            if isinstance(current_metadata_str, str):
                try:
                    current_metadata = json.loads(current_metadata_str)
                except (json.JSONDecodeError, TypeError):
                    current_metadata = {}
            else:
                current_metadata = current_metadata_str or {}

            # 合并新元数据
            current_metadata.update(metadata)

            # 优化 2：使用参数化查询确保 SQL 安全。SQLAlchemy 在部分
            # AstrBot 运行环境中不是显式依赖，缺失时使用原始 SQL。
            async with doc_storage.get_session() as session, session.begin():
                try:
                    from sqlalchemy import text

                    stmt = text(
                        "UPDATE documents SET metadata = :metadata, "
                        "updated_at = :updated_at WHERE id = :id"
                    )
                except ModuleNotFoundError:
                    stmt = (
                        "UPDATE documents SET metadata = :metadata, "
                        "updated_at = :updated_at WHERE id = :id"
                    )

                await session.execute(
                    stmt,
                    {
                        "metadata": json.dumps(current_metadata, ensure_ascii=False),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "id": doc_id,
                    },
                )

            logger.debug("[元数据更新] 成功")
            return True

        except Exception as exc:
            from astrbot.api import logger

            logger.error(
                "[元数据更新] 失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False

    async def _update_metadata_if_revision(
        self,
        doc_id: int,
        metadata: dict[str, Any],
        expected_revision: str,
    ) -> bool:
        """在 SQLite 写锁内校验 revision 后更新 metadata。"""

        import json

        from astrbot.api import logger

        doc_storage = self.faiss_db.document_storage
        try:
            from sqlalchemy import text
        except ModuleNotFoundError:
            return False

        session = None
        try:
            async with doc_storage.get_session() as session:
                await session.execute(text("BEGIN IMMEDIATE"))
                result = await session.execute(
                    text(
                        "SELECT metadata, created_at, updated_at "
                        "FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
                row = result.mappings().first()
                if row is None:
                    await session.rollback()
                    return False
                current_revision = row.get("updated_at") or row.get("created_at")
                if hasattr(current_revision, "isoformat"):
                    current_revision = current_revision.isoformat()
                if str(current_revision or "").strip() != str(expected_revision):
                    await session.rollback()
                    return False
                current_metadata = row.get("metadata")
                if isinstance(current_metadata, str):
                    try:
                        current_metadata = json.loads(current_metadata)
                    except (TypeError, json.JSONDecodeError):
                        current_metadata = {}
                if not isinstance(current_metadata, dict):
                    current_metadata = {}
                current_metadata.update(metadata)
                updated_at = datetime.now(timezone.utc).isoformat()
                update_result = await session.execute(
                    text(
                        "UPDATE documents SET metadata = :metadata, "
                        "updated_at = :updated_at "
                        "WHERE id = :id AND CAST(updated_at AS TEXT) = :revision"
                    ),
                    {
                        "metadata": json.dumps(current_metadata, ensure_ascii=False),
                        "updated_at": updated_at,
                        "id": doc_id,
                        "revision": str(expected_revision),
                    },
                )
                if update_result.rowcount != 1:
                    await session.rollback()
                    return False
                await session.commit()
                logger.debug("[元数据更新] revision 校验通过并完成原子更新")
                return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if session is not None:
                try:
                    await session.rollback()
                except Exception:
                    pass
            logger.error(
                "[元数据更新] revision 原子更新失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False

    async def update_content_if_revision(
        self,
        doc_id: int,
        content: str,
        metadata: dict[str, Any],
        expected_revision: str,
    ) -> bool:
        """在 canonical 写锁内原子替换正文、metadata 与向量。"""

        if not self.backend_capabilities.supports(AdapterCapability.UPDATE):
            return False
        if not content or not content.strip():
            return False

        import json
        import numpy as np

        from astrbot.api import logger

        doc_storage = self.faiss_db.document_storage
        embedding_provider = getattr(self.faiss_db, "embedding_provider", None)
        embedding_storage = getattr(self.faiss_db, "embedding_storage", None)
        if embedding_provider is None or embedding_storage is None:
            return False
        try:
            from sqlalchemy import text
        except ModuleNotFoundError:
            return False

        embedding_content = self._fit_content_for_embedding(content, 4000)
        vector = np.asarray(
            await embedding_provider.get_embedding(embedding_content),
            dtype=np.float32,
        )
        if vector.shape != (embedding_storage.dimension,):
            return False

        session = None
        try:
            async with doc_storage.get_session() as session:
                await session.execute(text("BEGIN IMMEDIATE"))
                result = await session.execute(
                    text(
                        "SELECT text, metadata, updated_at, created_at "
                        "FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
                row = result.mappings().first()
                if row is None:
                    await session.rollback()
                    return False
                current_revision = row.get("updated_at") or row.get("created_at")
                if hasattr(current_revision, "isoformat"):
                    current_revision = current_revision.isoformat()
                if str(current_revision or "").strip() != str(expected_revision):
                    await session.rollback()
                    return False

                current_metadata = row.get("metadata")
                if isinstance(current_metadata, str):
                    try:
                        current_metadata = json.loads(current_metadata)
                    except (TypeError, json.JSONDecodeError):
                        current_metadata = {}
                if not isinstance(current_metadata, dict):
                    current_metadata = {}
                current_metadata.update(metadata)
                updated_at = datetime.now(timezone.utc).isoformat()

                delete_fts = getattr(doc_storage, "_delete_fts_row", None)
                insert_fts = getattr(doc_storage, "_insert_fts_row", None)
                if callable(delete_fts) and callable(insert_fts):
                    await delete_fts(session, doc_id, str(row.get("text") or ""))
                update_result = await session.execute(
                    text(
                        "UPDATE documents SET text = :content, metadata = :metadata, "
                        "updated_at = :updated_at "
                        "WHERE id = :id AND CAST(updated_at AS TEXT) = :revision"
                    ),
                    {
                        "content": content,
                        "metadata": json.dumps(current_metadata, ensure_ascii=False),
                        "updated_at": updated_at,
                        "id": doc_id,
                        "revision": str(expected_revision),
                    },
                )
                if update_result.rowcount != 1:
                    await session.rollback()
                    return False
                if callable(delete_fts) and callable(insert_fts):
                    await insert_fts(session, doc_id, content)
                await embedding_storage.delete([doc_id])
                await embedding_storage.insert(vector, doc_id)
                await session.commit()
                logger.debug("[正文更新] revision 校验通过并完成原子更新")
                return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if session is not None:
                try:
                    await session.rollback()
                except Exception:
                    pass
            logger.error(
                "[正文更新] revision 原子更新失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False

    async def delete_document(self, doc_id: int) -> bool:
        """
        删除文档（修复版：正确使用 `FaissVecDB.delete` 接口并结合缓存优化）

        参数:
            doc_id: 文档 ID（documents 表中的整数 id）

        返回:
            是否成功删除。
        """
        if not self.backend_capabilities.supports(AdapterCapability.DELETE):
            return False

        from astrbot.api import logger

        try:
            # 优化 3：使用带缓存的 UUID 查询方法
            uuid_doc_id = await self._get_uuid_from_id(doc_id)

            if not uuid_doc_id:
                logger.warning("[向量删除] 文档不存在或缺少 UUID")
                return False

            # 使用 UUID 调用 FaissVecDB.delete()
            # 这会同时删除 document_storage 和 embedding_storage
            await self.faiss_db.delete(uuid_doc_id)

            # 从缓存中移除
            self._id_cache.pop(doc_id, None)

            logger.debug("[向量删除] 成功")
            return True

        except Exception as exc:
            from astrbot.api import logger

            logger.error(
                "[向量删除] 失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False
