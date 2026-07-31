"""BM25 全文索引重建操作。"""

from typing import Any

import aiosqlite
from astrbot.api import logger

from ..storage.base import apply_perf_pragmas


class Bm25RebuilderMixin:
    """BM25 索引重建逻辑 -- 从 documents 表重建 FTS 表。"""

    async def _rebuild_bm25_index(
        self,
        memory_engine: Any,
        total: int,
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        bm25_retriever = getattr(memory_engine, "bm25_retriever", None)
        text_processor = getattr(bm25_retriever, "text_processor", None)
        if text_processor is None:
            text_processor = getattr(memory_engine, "text_processor", None)
        if text_processor is None:
            raise RuntimeError("无法重建 BM25：TextProcessor 未初始化")

        raw_table_name = getattr(bm25_retriever, "fts_table", "memora_memories_fts")
        if not isinstance(raw_table_name, str) or not raw_table_name.strip():
            raw_table_name = "memora_memories_fts"
        table_name = self._validate_fts_table_name(raw_table_name)
        batch_size = int(options["batch_size"])
        max_failure_ratio = float(options["max_failure_ratio"])

        await self._clear_bm25_with_retry(table_name)
        processed = 0
        failed_ids: set[int] = set()

        async for batch in self._iter_document_batches(batch_size):
            rows_to_insert: list[tuple[int, str]] = []
            for doc_id, _doc_uuid, text, _metadata_json in batch:
                try:
                    if hasattr(text_processor, "preprocess_for_bm25"):
                        processed_content = text_processor.preprocess_for_bm25(
                            text or ""
                        )
                    else:
                        tokens = text_processor.tokenize(text or "", True)
                        processed_content = " ".join(tokens)
                    rows_to_insert.append((int(doc_id), processed_content))
                except Exception as e:
                    failed_ids.add(int(doc_id))
                    logger.error(f"BM25 预处理失败 doc_id={doc_id}: {e}")

            if rows_to_insert:
                try:
                    async with aiosqlite.connect(self.db_path) as db:
                        await apply_perf_pragmas(db)
                        await db.executemany(
                            """INSERT INTO memora_memories_fts(doc_id, content)
                               VALUES (:doc_id, :content)""",
                            [
                                {"doc_id": doc_id, "content": content}
                                for doc_id, content in rows_to_insert
                            ],
                        )
                        await db.commit()
                    processed += len(rows_to_insert)
                except Exception as batch_error:
                    logger.warning(f"BM25 批量写入失败，将逐条重试: {batch_error}")
                    for row_doc_id, processed_content in rows_to_insert:
                        try:
                            async with aiosqlite.connect(self.db_path) as db:
                                await apply_perf_pragmas(db)
                                await db.execute(
                                    """INSERT INTO memora_memories_fts(doc_id, content)
                                       VALUES (:doc_id, :content)""",
                                    {
                                        "doc_id": row_doc_id,
                                        "content": processed_content,
                                    },
                                )
                                await db.commit()
                            processed += 1
                        except Exception as e:
                            failed_ids.add(int(row_doc_id))
                            logger.error(f"BM25 写入失败 doc_id={row_doc_id}: {e}")

            if progress_callback:
                await progress_callback(
                    processed,
                    total,
                    f"BM25 已处理 {processed}/{total} 条",
                )

            if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                logger.error(
                    f"BM25 重建失败率过高: {len(failed_ids)}/{total}，停止后续重建"
                )
                break

        return {
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
        }
