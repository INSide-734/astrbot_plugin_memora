"""向量索引重建与修复操作。"""

import asyncio
import contextlib
import os
from typing import Any

import faiss
import numpy as np
from astrbot.api import logger


class VectorRebuilderMixin:
    """向量（FAISS）索引重建与增量修复逻辑。"""

    async def _repair_missing_vectors(
        self,
        memory_engine: Any,
        missing_ids: set[int],
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        faiss_db = getattr(memory_engine, "faiss_db", None)
        embedding_storage = getattr(faiss_db, "embedding_storage", None)
        provider = getattr(faiss_db, "embedding_provider", None)
        if embedding_storage is None or provider is None:
            raise RuntimeError("无法修复向量索引：Embedding 组件未初始化")

        total = len(missing_ids)
        processed = 0
        failed_ids: set[int] = set()
        batch_delay = float(options["batch_delay"])
        max_failure_ratio = float(options["max_failure_ratio"])
        batch_index = 0

        async for batch in self._iter_document_batches(
            int(options["batch_size"]), missing_ids
        ):
            batch_index += 1
            ids = [int(row[0]) for row in batch]
            contents = [row[2] or "" for row in batch]
            logger.info(
                "向量补写批次开始: "
                f"batch={batch_index}, size={len(ids)}, "
                f"id_range={ids[0]}-{ids[-1]}, processed={processed}/{total}, "
                f"failed={len(failed_ids)}"
            )
            try:
                vectors = await self._embed_batch_with_retry(
                    provider, contents, options
                )
                vectors_array = np.asarray(vectors, dtype=np.float32)
                if vectors_array.ndim != 2 or len(vectors_array) != len(ids):
                    raise ValueError(
                        f"Embedding 返回数量不匹配: 期望 {len(ids)}，实际 {len(vectors_array)}"
                    )
                await embedding_storage.insert_batch(vectors_array, ids)
                processed += len(ids)
            except Exception as e:
                failed_ids.update(ids)
                logger.error(f"向量补写批次失败 ids={ids[:3]}...: {e}", exc_info=True)

            if progress_callback:
                await progress_callback(
                    processed,
                    total,
                    f"向量补写已处理 {processed}/{total} 条",
                )

            logger.info(
                "向量补写进度: "
                f"processed={processed}/{total}, failed={len(failed_ids)}, "
                f"failure_ratio={self._failure_ratio(len(failed_ids), total):.2%}"
            )

            if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                break
            if batch_delay > 0:
                await asyncio.sleep(batch_delay)

        return {
            "mode": "repair",
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
            "switched": False,
            "partial": len(failed_ids) > 0,
        }

    async def _rebuild_vector_index_full(
        self,
        memory_engine: Any,
        total: int,
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        faiss_db = getattr(memory_engine, "faiss_db", None)
        embedding_storage = getattr(faiss_db, "embedding_storage", None)
        provider = getattr(faiss_db, "embedding_provider", None)
        if embedding_storage is None or provider is None:
            raise RuntimeError("无法重建向量索引：Embedding 组件未初始化")

        dimension = int(getattr(embedding_storage, "dimension", 0) or 0)
        if dimension <= 0:
            raise RuntimeError("无法重建向量索引：索引维度无效")

        temp_index = faiss.IndexIDMap(faiss.IndexFlatL2(dimension))
        processed = 0
        failed_ids: set[int] = set()
        batch_delay = float(options["batch_delay"])
        max_failure_ratio = float(options["max_failure_ratio"])
        batch_index = 0

        async for batch in self._iter_document_batches(int(options["batch_size"])):
            batch_index += 1
            ids = [int(row[0]) for row in batch]
            contents = [row[2] or "" for row in batch]
            logger.info(
                "向量重建批次开始: "
                f"batch={batch_index}, size={len(ids)}, "
                f"id_range={ids[0]}-{ids[-1]}, processed={processed}/{total}, "
                f"failed={len(failed_ids)}"
            )
            try:
                vectors = await self._embed_batch_with_retry(
                    provider, contents, options
                )
                vectors_array = np.asarray(vectors, dtype=np.float32)
                if vectors_array.ndim != 2 or len(vectors_array) != len(ids):
                    raise ValueError(
                        f"Embedding 返回数量不匹配: 期望 {len(ids)}，实际 {len(vectors_array)}"
                    )
                if vectors_array.shape[1] != dimension:
                    raise ValueError(
                        f"Embedding 维度不匹配: 期望 {dimension}，实际 {vectors_array.shape[1]}"
                    )
                temp_index.add_with_ids(vectors_array, np.asarray(ids, dtype=np.int64))
                processed += len(ids)
            except Exception as e:
                failed_ids.update(ids)
                logger.error(f"向量重建批次失败 ids={ids[:3]}...: {e}", exc_info=True)

            if progress_callback:
                await progress_callback(
                    processed,
                    total,
                    f"向量索引已处理 {processed}/{total} 条",
                )

            logger.info(
                "向量重建进度: "
                f"processed={processed}/{total}, failed={len(failed_ids)}, "
                f"failure_ratio={self._failure_ratio(len(failed_ids), total):.2%}"
            )

            if self._failure_ratio(len(failed_ids), total) > max_failure_ratio:
                logger.error(
                    f"向量重建失败率过高: {len(failed_ids)}/{total}，不会切换新索引"
                )
                return {
                    "mode": "full",
                    "processed": processed,
                    "errors": len(failed_ids),
                    "failed_ids": failed_ids,
                    "switched": False,
                    "partial": True,
                }
            if batch_delay > 0:
                await asyncio.sleep(batch_delay)

        if total > 0 and processed == 0:
            return {
                "mode": "full",
                "processed": 0,
                "errors": max(total, len(failed_ids)),
                "failed_ids": failed_ids,
                "switched": False,
                "partial": True,
            }

        index_path = getattr(embedding_storage, "path", None)
        if index_path:
            temp_path = f"{index_path}.rebuild.tmp"
            try:
                faiss.write_index(temp_index, temp_path)
                os.replace(temp_path, index_path)
            finally:
                if os.path.exists(temp_path):
                    with contextlib.suppress(OSError):
                        os.remove(temp_path)

        embedding_storage.index = temp_index
        return {
            "mode": "full",
            "processed": processed,
            "errors": len(failed_ids),
            "failed_ids": failed_ids,
            "switched": True,
            "partial": len(failed_ids) > 0,
        }

    async def _rebuild_or_repair_vector_index(
        self,
        memory_engine: Any,
        total: int,
        options: dict[str, Any],
        progress_callback=None,
    ) -> dict[str, Any]:
        document_ids = await self._get_document_ids()
        if not document_ids:
            return {
                "mode": "skip",
                "processed": 0,
                "errors": 0,
                "failed_ids": set(),
                "switched": False,
                "partial": False,
            }

        vector_ids = self._get_vector_ids()
        vector_count = self._get_vector_count()
        if vector_ids is not None:
            missing_ids = document_ids - vector_ids
            if not missing_ids:
                return {
                    "mode": "skip",
                    "processed": 0,
                    "errors": 0,
                    "failed_ids": set(),
                    "switched": False,
                    "partial": False,
                }
            if vector_ids:
                logger.info(f"检测到 {len(missing_ids)} 条向量缺失，执行增量补写")
                return await self._repair_missing_vectors(
                    memory_engine, missing_ids, options, progress_callback
                )

        if vector_ids is None and vector_count >= total:
            logger.info("向量索引计数不小于 documents 数量，跳过全量向量重建")
            return {
                "mode": "skip",
                "processed": 0,
                "errors": 0,
                "failed_ids": set(),
                "switched": False,
                "partial": False,
            }

        logger.info("向量索引缺失或为空，执行安全全量重建")
        return await self._rebuild_vector_index_full(
            memory_engine, total, options, progress_callback
        )
