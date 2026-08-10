"""回填调度器：使用向量聚类重新拆分旧版混合话题记忆。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import Mock

from astrbot.api import logger

from ....processors.topic_splitter import (
    EmbeddingClusteringStrategy,
    _safe_bool,
)


class BackfillScheduler:
    """后台任务：重新拆分旧版记忆（``schema_version < "v3"``）。"""

    def __init__(
        self,
        memory_engine: Any = None,
        config: dict[str, Any] | None = None,
        embed_fn: Any = None,
    ) -> None:
        """初始化回填任务依赖、资源上限和内存进度。

        参数:
            memory_engine: 提供文档读取、记忆写入和删除能力的运行时门面。
            config: 回填启用状态、批次大小和单轮上限配置。
            embed_fn: 话题聚类使用的异步向量生成函数。
        """

        self._engine = memory_engine
        config = config or {}
        self._enabled = _safe_bool(config.get("enabled", True))
        self._batch_size = int(config.get("batch_size", 50))
        self._max_per_run = int(config.get("max_backfill_per_run", 500))

        cluster_cfg = {
            "similarity_threshold": 0.5,
            "min_cluster_size": 1,
            "max_clusters": 5,
        }
        self._cluster_strategy = EmbeddingClusteringStrategy(
            cluster_cfg,
            embed_fn=embed_fn,
        )

        self._job_id: str | None = None
        self._progress: dict[str, Any] = {
            "total": 0,
            "processed": 0,
            "status": "idle",
            "errors": 0,
        }
        # 游标只覆盖当前运行；读取首批数据后才会变为非零值。
        self._checkpoint: int = 0
        self._task: asyncio.Task | None = None

    # ---- 对外 API ----

    @property
    def progress(self) -> dict[str, Any]:
        """返回当前回填进度的只读副本。"""

        return dict(self._progress)

    @property
    def is_running(self) -> bool:
        """返回是否存在状态为运行中的回填任务。"""

        return self._progress.get("status") == "running"

    async def start(self) -> str:
        """启动回填任务，并返回 ``job_id``。

        返回:
            当前任务生成的稳定运行期标识。

        异常:
            RuntimeError: 回填已禁用或已有任务正在运行。
        """

        if not self._enabled:
            raise RuntimeError("legacy backfill disabled")
        if self.is_running:
            raise RuntimeError("A backfill job is already running")

        self._job_id = f"bf_{int(time.time())}"
        self._progress = {
            "job_id": self._job_id,
            "total": 0,
            "processed": 0,
            "status": "running",
            "errors": 0,
            "started_at": time.time(),
        }
        self._checkpoint = 0

        self._task = asyncio.create_task(self._run())
        logger.info("[Backfill] 任务 %s 已启动", self._job_id)
        return self._job_id

    async def get_status(self) -> dict[str, Any]:
        """返回当前回填进度的独立副本。"""

        return dict(self._progress)

    async def stop(self) -> None:
        """停止当前正在运行的回填任务（如果存在）。"""

        task = self._task
        if task is None or task.done():
            return
        self._progress["status"] = "stopping"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._progress["status"] = "cancelled"
            self._progress["cancelled_at"] = time.time()
            self._task = None

    # ---- 内部实现 ----

    async def _run(self) -> None:
        """按批次执行当前回填任务并维护终态与计数。"""

        try:
            processed = 0
            while processed < self._max_per_run:
                batch = await self._fetch_legacy_batch()
                if not batch:
                    logger.info("[Backfill] 没有更多旧版记忆需要处理")
                    break

                for doc_id, raw_metadata in batch:
                    try:
                        await self._backfill_one(doc_id, raw_metadata)
                        processed += 1
                        self._progress["processed"] = processed
                    except Exception as exc:
                        logger.error(
                            "[Backfill] 处理记忆 %d 失败: %s",
                            doc_id,
                            exc,
                        )
                        self._progress["errors"] += 1

                    self._checkpoint = doc_id

                if processed >= self._max_per_run:
                    logger.info(
                        "[Backfill] 已达到单轮处理上限 %d，暂停任务",
                        self._max_per_run,
                    )

            if self._progress.get("errors", 0):
                self._progress["status"] = "completed_with_errors"
            else:
                self._progress["status"] = "completed"
            self._progress["completed_at"] = time.time()
            logger.info(
                "[Backfill] 任务 %s 已完成：处理 %d 条，失败 %d 条",
                self._job_id,
                processed,
                self._progress["errors"],
            )
        except asyncio.CancelledError:
            self._progress["status"] = "cancelled"
            self._progress["cancelled_at"] = time.time()
            logger.info("[Backfill] 任务 %s 已取消", self._job_id)
            raise
        except Exception as exc:
            self._progress["status"] = "failed"
            self._progress["error"] = str(exc)
            logger.error("[Backfill] 任务 %s 失败: %s", self._job_id, exc)
        finally:
            self._task = None

    async def _fetch_legacy_batch(self) -> list[tuple[int, dict]]:
        """获取一批尚未完成回填的旧版记忆。"""

        if self._engine is None or self._engine.faiss_db is None:
            return []

        try:
            ds = self._engine.faiss_db.document_storage
            docs = await self._fetch_document_page(ds)
            results: list[tuple[int, dict]] = []
            for doc in docs:
                doc_id = doc.get("id")
                if doc_id is None or doc_id <= self._checkpoint:
                    continue
                meta = doc.get("metadata", {})
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                schema_ver = meta.get("schema_version", "")
                if schema_ver and str(schema_ver).startswith("v"):
                    try:
                        num_ver = int(str(schema_ver)[1:])
                    except ValueError:
                        num_ver = 0
                    if num_ver >= 3:
                        continue
                key_facts = meta.get("key_facts", [])
                if isinstance(key_facts, list) and len(key_facts) <= 1:
                    continue
                results.append((doc_id, meta))
                if len(results) >= self._batch_size:
                    break
            return results
        except Exception:
            logger.warning("[Backfill] 获取回填批次失败", exc_info=True)
            return []

    async def _fetch_document_page(self, document_storage: Any) -> list[dict]:
        """按能力优先级读取 checkpoint 之后的一页文档。

        参数:
            document_storage: FAISS 关联的文档存储对象。

        返回:
            含文档 ID 与 metadata 的字典列表。
        """

        get_after_id = getattr(document_storage, "get_documents_after_id", None)
        if self._is_async_callable(get_after_id):
            return await get_after_id(
                last_id=self._checkpoint,
                limit=self._batch_size,
            )

        db = getattr(self._engine, "db_connection", None)
        if db is not None and not isinstance(db, Mock) and hasattr(db, "execute"):
            cursor = await db.execute(
                """
                SELECT id, metadata
                FROM documents
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                (self._checkpoint, self._batch_size),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"] if hasattr(row, "keys") else row[0],
                    "metadata": row["metadata"] if hasattr(row, "keys") else row[1],
                }
                for row in rows
            ]

        get_documents = getattr(document_storage, "get_documents", None)
        if self._is_async_callable(get_documents):
            return await get_documents(
                metadata_filters={},
                limit=self._batch_size,
                offset=self._checkpoint,
            )

        return await document_storage.get_all_documents(limit=self._batch_size)

    @staticmethod
    def _is_async_callable(candidate: Any) -> bool:
        """判断候选对象是否为异步可调用对象。"""

        return asyncio.iscoroutinefunction(candidate)

    async def _backfill_one(self, doc_id: int, meta: dict) -> None:
        """将单条旧版记忆重新拆分为话题更一致的片段。

        参数:
            doc_id: 待回填的 canonical 文档 ID。
            meta: 旧文档的 metadata 副本。

        异常:
            RuntimeError: 新片段全部写入后，旧文档删除失败。
        """

        key_facts = meta.get("key_facts", [])
        if not key_facts or len(key_facts) <= 1:
            return

        data = {
            "summary": meta.get("summary", meta.get("persona_summary", "")),
            "key_facts": key_facts,
            "topics": meta.get("topics", []),
            "importance": meta.get("importance", 0.5),
            "sentiment": meta.get("sentiment", "neutral"),
            "emotion_tags": meta.get("emotion_tags", []),
        }

        segments = await self._cluster_strategy.segment(data)

        if len(segments) <= 1:
            if self._engine:
                await self._engine.hybrid_retriever.update_metadata(
                    doc_id,
                    {"schema_version": "v3"},
                    advance_revision=False,
                )
            return

        session_id = meta.get("session_id") or meta.get("source_window", {}).get(
            "session_id"
        )
        persona_id = meta.get("persona_id")
        target_count = len(segments)
        new_ids: list[int] = []
        for seg in segments:
            seg.metadata["schema_version"] = "v3"
            seg.metadata["backfill_source"] = doc_id
            try:
                if self._engine:
                    new_id = await self._engine.add_memory(
                        content=seg.content,
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=seg.importance,
                        metadata=seg.metadata,
                        atoms=seg.atoms,
                    )
                    new_ids.append(new_id)
            except Exception:
                logger.warning("[Backfill] 写入文档 %d 的新片段失败", doc_id)

        # 只有全部新片段成功写入后，才允许删除旧记忆。
        if len(new_ids) == target_count:
            try:
                if self._engine:
                    await self._engine.delete_memory(doc_id)
            except Exception as exc:
                if self._engine and getattr(self._engine, "hybrid_retriever", None):
                    try:
                        await self._engine.hybrid_retriever.update_metadata(
                            doc_id,
                            {
                                "schema_version": "v3",
                                "backfill_delete_failed": True,
                                "backfill_new_ids": list(new_ids),
                            },
                            advance_revision=False,
                        )
                    except Exception:
                        logger.warning(
                            "[Backfill] 标记旧记忆 %d 的删除失败状态时出错",
                            doc_id,
                            exc_info=True,
                        )
                logger.warning("[Backfill] 删除旧记忆 %d 失败", doc_id)
                raise RuntimeError(str(exc)) from exc

            logger.debug(
                "[Backfill] 已将文档 %d（%d 条事实）拆分为 %d 条记忆",
                doc_id,
                len(key_facts),
                len(new_ids),
            )
        else:
            logger.warning(
                "[Backfill] 文档 %d 仅写入 %d/%d 个片段，保留旧记忆",
                doc_id,
                len(new_ids),
                target_count,
            )


__all__ = ["BackfillScheduler"]
