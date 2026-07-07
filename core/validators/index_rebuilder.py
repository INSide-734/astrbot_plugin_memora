"""IndexValidator 的索引重建操作。"""

from typing import Any

import aiosqlite

from astrbot.api import logger

from ..storage.base import apply_perf_pragmas
from .bm25_rebuilder import Bm25RebuilderMixin
from .embedding_retry import EmbeddingRetryMixin
from .vector_rebuilder import VectorRebuilderMixin


class IndexRebuilderMixin(
    Bm25RebuilderMixin, EmbeddingRetryMixin, VectorRebuilderMixin
):
    """BM25 与向量索引重建及备份恢复的编排器。"""

    async def rebuild_indexes(
        self, memory_engine: Any, progress_callback=None
    ) -> dict[str, Any]:
        """
        分批安全重建索引

        安全策略：
        1. documents 表只读，始终作为原始数据源。
        2. BM25 直接按 documents 分批重建。
        3. 向量索引优先增量补缺；需要全量重建时先构建临时 FAISS 索引。
        4. 失败率超过阈值时不切换全量重建的新向量索引。

        Args:
            memory_engine: MemoryEngine实例
            progress_callback: 进度回调函数 (current, total, message)

        Returns:
            Dict: 重建结果
        """
        try:
            logger.info("开始分批安全重建索引。")
            options = self._get_rebuild_options(memory_engine)
            total = await self._get_document_count()

            if total <= 0:
                return {
                    "success": True,
                    "message": "没有需要重建的文档",
                    "processed": 0,
                    "errors": 0,
                    "total": 0,
                    "partial": False,
                    "switched": False,
                }

            logger.info(
                "重建参数: "
                f"total={total}, batch_size={options['batch_size']}, "
                f"embedding_batch_size={options['embedding_batch_size']}, "
                f"tasks_limit={options['tasks_limit']}, "
                f"request_delay={options['request_delay']}, "
                f"batch_delay={options['batch_delay']}, "
                f"max_failure_ratio={options['max_failure_ratio']}"
            )

            bm25_result = await self._rebuild_bm25_index(
                memory_engine, total, options, progress_callback
            )
            bm25_failed_ids = set(bm25_result["failed_ids"])
            if self._failure_ratio(len(bm25_failed_ids), total) > float(
                options["max_failure_ratio"]
            ):
                message = (
                    f"BM25 重建失败率过高: {len(bm25_failed_ids)}/{total}。"
                    "documents 原始数据未被删除，已停止向量重建。"
                )
                logger.error(message)
                return {
                    "success": False,
                    "message": message,
                    "processed": total - len(bm25_failed_ids),
                    "errors": len(bm25_failed_ids),
                    "total": total,
                    "partial": True,
                    "switched": False,
                    "bm25_processed": bm25_result["processed"],
                    "bm25_errors": bm25_result["errors"],
                    "vector_processed": 0,
                    "vector_errors": 0,
                    "failure_ratio": self._failure_ratio(len(bm25_failed_ids), total),
                }

            vector_result = await self._rebuild_or_repair_vector_index(
                memory_engine, total, options, progress_callback
            )
            vector_failed_ids = set(vector_result["failed_ids"])
            failed_ids = bm25_failed_ids | vector_failed_ids
            failure_ratio = self._failure_ratio(len(failed_ids), total)
            accepted = failure_ratio <= float(options["max_failure_ratio"])
            partial = bool(failed_ids)

            if accepted:
                message = (
                    "索引重建完成"
                    if not partial
                    else (
                        "索引已按失败率阈值完成可接受切换，"
                        f"仍有 {len(failed_ids)} 条需后续重试"
                    )
                )
            else:
                message = (
                    f"索引重建失败率过高: {len(failed_ids)}/{total}。"
                    "全量向量重建未切换新索引，documents 原始数据未被删除。"
                )

            logger.info(
                "索引重建结果: "
                f"accepted={accepted}, partial={partial}, "
                f"bm25={bm25_result['processed']}/{total}, "
                f"vector={vector_result['processed']}/{total}, "
                f"errors={len(failed_ids)}, vector_mode={vector_result['mode']}"
            )

            return {
                "success": accepted,
                "message": message,
                "processed": max(0, total - len(failed_ids)),
                "errors": len(failed_ids),
                "total": total,
                "partial": partial,
                "switched": bool(vector_result["switched"]),
                "bm25_processed": bm25_result["processed"],
                "bm25_errors": bm25_result["errors"],
                "vector_processed": vector_result["processed"],
                "vector_errors": vector_result["errors"],
                "vector_mode": vector_result["mode"],
                "failure_ratio": failure_ratio,
            }

        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            return {
                "success": False,
                "message": (
                    f"重建索引失败: {str(e)}。documents 原始数据未被删除，"
                    "请查看日志后重试 /memora rebuild-index。"
                ),
                "error": str(e),
            }

    async def _try_restore_from_backup(self) -> None:
        """
        重建失败时尝试从备份表恢复 documents 数据。
        仅在备份表存在且 documents 表为空时执行恢复。
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await apply_perf_pragmas(db)

                # 检查备份表是否存在
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='_documents_rebuild_backup'
                """)
                if not await cursor.fetchone():
                    return

                # 只在 documents 表为空时恢复（避免覆盖部分重建的数据）
                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                row = await cursor.fetchone()
                doc_count = row[0] if row else 0

                if doc_count > 0:
                    logger.warning(
                        f"documents 表已有 {doc_count} 条数据，跳过备份恢复（避免重复）"
                    )
                    return

                logger.warning("检测到重建失败且 documents 表为空，正在从备份表恢复...")
                await db.execute("""
                    INSERT INTO documents (id, doc_id, text, metadata, created_at, updated_at)
                    SELECT id, doc_id, text, metadata, created_at, updated_at
                    FROM _documents_rebuild_backup
                """)
                await db.commit()

                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                row = await cursor.fetchone()
                restored = row[0] if row else 0
                logger.info(
                    f"已从备份表恢复 {restored} 条记忆数据，BM25/向量索引需手动重建"
                )

        except Exception as e:
            logger.error(f"从备份表恢复失败: {e}", exc_info=True)
