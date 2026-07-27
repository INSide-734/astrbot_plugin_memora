"""
记忆生命周期管理 —— 负责添加、更新与删除记忆。

管理记忆在多个存储层（向量库、BM25 索引、documents 表）中的
创建、更新和删除操作，包含事务性回滚机制。
"""

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger

from .bm25_retriever import BM25Retriever
from .vector_retriever import VectorRetriever


class MemoryLifecycleManager:
    """
    记忆生命周期管理器

    负责记忆在多个存储层中的统一生命周期操作：
    - 添加：先写入向量库获取 doc_id，再同步写入 BM25 索引
    - 更新：通过向量库更新元数据（自动同步 DocumentStorage）
    - 删除：按 BM25 → 向量库 → documents 表顺序删除，失败时回滚 BM25
    """

    def __init__(
        self,
        bm25_retriever: BM25Retriever,
        vector_retriever: VectorRetriever,
    ):
        """
        初始化生命周期管理器

        参数:
            bm25_retriever: BM25 检索器实例
            vector_retriever: 向量检索器实例
        """
        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever

    async def add_memory(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> int:
        """
        添加记忆到两个索引

        参数:
            content: 记忆内容
            metadata: 元数据（必须包含：importance、create_time、
                last_access_time、session_id、persona_id）

        返回:
            两个索引中一致的文档 ID。
        """
        # 确保元数据存在
        metadata = metadata or {}

        # 补充默认元数据
        if "importance" not in metadata:
            metadata["importance"] = 0.5
        if "create_time" not in metadata:
            metadata["create_time"] = time.time()
        if "last_access_time" not in metadata:
            metadata["last_access_time"] = time.time()
        if "session_id" not in metadata:
            metadata["session_id"] = None
        if "persona_id" not in metadata:
            metadata["persona_id"] = None

        # 先写入向量库以获取 doc_id
        doc_id = await self.vector_retriever.add_document(content, metadata)

        # 使用同一个 doc_id 写入 BM25 索引
        await self.bm25_retriever.add_document(doc_id, content, metadata)

        return doc_id

    async def update_metadata(
        self,
        doc_id: int,
        metadata: dict[str, Any],
        expected_revision: str | None = None,
    ) -> bool:
        """
        同步更新所有存储层的元数据

        ID 体系说明：
        - doc_id（int）：documents 表的主键，也是统一标识符
        - 三个存储层都使用这个整数 ID 进行关联
        - FAISS 内部使用 UUID，但对外接口使用整数 ID

        更新策略：
        1. FAISS 向量库（通过 vector_retriever，会更新 DocumentStorage）
        2. documents 表无需额外更新（已在步骤 1 完成）
        3. BM25 索引不存储 metadata，而是从 documents 表读取

        参数:
            doc_id: 文档 ID（整数）
            metadata: 新的元数据字典
            expected_revision: 可选的 source revision；提供时拒绝 stale writer

        返回:
            是否更新成功。
        """
        try:
            # 更新 FAISS 向量库（会同步更新 DocumentStorage 中的 metadata）
            if expected_revision is None:
                vector_success = await self.vector_retriever.update_metadata(
                    doc_id,
                    metadata,
                )
            else:
                vector_success = await self.vector_retriever.update_metadata(
                    doc_id,
                    metadata,
                    expected_revision=expected_revision,
                )

            if not vector_success:
                logger.error("[同步更新] FAISS 更新失败")
                return False

            logger.info("[同步更新] 元数据更新成功")
            return True

        except Exception as exc:
            logger.error(
                "[同步更新] 失败，异常类型=%s",
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
        """委托向量层执行带 revision CAS 的正文更新，并刷新 BM25。"""

        try:
            canonical_success = await self.vector_retriever.update_content_if_revision(
                doc_id,
                content,
                metadata,
                expected_revision,
            )
            if not canonical_success:
                return False
            bm25_success = await self.bm25_retriever.update_document(
                doc_id,
                content,
                metadata,
            )
            if not bm25_success:
                logger.warning("[正文更新] BM25 派生索引刷新失败，保留 canonical 提交")
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[正文更新] 生命周期同步失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False

    async def delete_memory(self, doc_id: int) -> bool:
        """
        从多个存储层中删除记忆（带事务回滚机制）

        参数:
            doc_id: 文档 ID

        返回:
            是否成功删除。
        """
        backup_content: str | None = None
        backup_metadata: dict[str, Any] = {}

        try:
            # 先备份原始文档，便于后续在失败时恢复 BM25 索引。
            try:
                async with self.bm25_retriever._connect() as db:
                    cursor = await db.execute(
                        "SELECT text, metadata FROM documents WHERE id = ?", (doc_id,)
                    )
                    row = await cursor.fetchone()
                    if row:
                        backup_content = row[0]
                        metadata_raw = row[1]
                        if isinstance(metadata_raw, str) and metadata_raw:
                            try:
                                backup_metadata = json.loads(metadata_raw)
                            except (json.JSONDecodeError, TypeError):
                                backup_metadata = {}
                        elif isinstance(metadata_raw, dict):
                            backup_metadata = metadata_raw
            except Exception as exc:
                logger.warning(
                    "[删除] 备份文档内容失败，异常类型=%s",
                    exc.__class__.__name__,
                )

            # 优先删除 BM25 索引（外键引用）
            try:
                bm25_deleted = await self.bm25_retriever.delete_document(doc_id)
                if not bm25_deleted:
                    logger.warning("[删除] BM25 索引删除失败")
                    return False
                logger.debug("[删除] BM25 索引已删除")
            except Exception as exc:
                logger.error(
                    "[删除] BM25 删除异常，异常类型=%s",
                    exc.__class__.__name__,
                )
                return False

            # 再删除向量库（主数据）
            try:
                vector_deleted = await self.vector_retriever.delete_document(doc_id)
                if not vector_deleted:
                    logger.error("[删除] 向量库删除失败，需回滚")
                    # 回滚：恢复 BM25 索引
                    await self._rollback_bm25_delete(
                        doc_id, backup_content, backup_metadata
                    )
                    return False
                logger.debug("[删除] 向量库已删除")
            except Exception as exc:
                logger.error(
                    "[删除] 向量删除异常，回滚 BM25，异常类型=%s",
                    exc.__class__.__name__,
                )
                # 回滚：恢复 BM25 索引
                await self._rollback_bm25_delete(
                    doc_id, backup_content, backup_metadata
                )
                return False

            # 最后删除 documents 表记录
            try:
                async with self.bm25_retriever._connect() as db:
                    await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    await db.commit()
                logger.debug("[删除] documents 表已删除")
            except Exception as exc:
                logger.warning(
                    "[删除] documents 表删除失败，异常类型=%s",
                    exc.__class__.__name__,
                )
                # documents 表删除失败不影响整体，因为主要数据已删除

            logger.info("[删除] 记忆删除成功")
            return True

        except Exception as exc:
            logger.error(
                "[删除] 删除记忆失败，异常类型=%s",
                exc.__class__.__name__,
            )
            return False

    async def _rollback_bm25_delete(
        self,
        doc_id: int,
        content: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        回滚 BM25 删除操作（尽力而为，实际恢复索引）

        参数:
            doc_id: 文档 ID
            content: 删除前备份的文档文本
            metadata: 文档元数据（可选）
        """
        if not content:
            logger.error(
                "[回滚] 缺少备份文本，无法恢复 BM25 索引；建议执行索引重建修复一致性"
            )
            return

        try:
            rollback_ok = await self.bm25_retriever.update_document(
                doc_id, content, metadata or {}
            )
            if rollback_ok:
                logger.info("[回滚] 已恢复 BM25 索引")
            else:
                logger.error("[回滚] BM25 索引恢复失败，建议执行索引重建")
        except Exception as exc:
            logger.error(
                "[回滚] BM25 索引恢复异常，异常类型=%s",
                exc.__class__.__name__,
            )


__all__ = ["MemoryLifecycleManager"]
