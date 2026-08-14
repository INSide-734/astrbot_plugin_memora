"""
统计和维护操作 Mixin
提供统计信息、存储维护和图像索引重建功能。

作为 Mixin 类使用，需要宿主类在 __init__ 中设置:
- self._config: dict
- self._db: 数据库连接
- self._db_path: 数据库路径
- self._faiss_db: FAISS 数据库
- self._graph_memory_manager: 图记忆管理器
- self._graph_store: 图存储
- self._invalidate_cache: 缓存失效回调
"""

import asyncio
import inspect
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from astrbot.api import logger

from ....shared.number_utils import clamp_float, safe_float
from ....shared.sql import (
    MEMORY_FTS_OPTIMIZE_SQL,
    MEMORY_FTS_TABLE,
)
from ...decay.application.operations import _normalize_batch_metadata

_TREND_DAYS = 90
_MILLISECOND_TIMESTAMP_THRESHOLD = 100_000_000_000


def _normalize_unix_timestamp(value: Any) -> float | None:
    timestamp = safe_float(value, -1.0)
    if timestamp > _MILLISECOND_TIMESTAMP_THRESHOLD:
        timestamp /= 1000.0
    return timestamp if timestamp >= 0 else None


def _recent_utc_date(value: Any, *, today: date) -> str | None:
    timestamp = _normalize_unix_timestamp(value)
    if timestamp is None:
        return None
    try:
        memory_date = datetime.fromtimestamp(timestamp, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None
    first_date = today - timedelta(days=_TREND_DAYS - 1)
    if memory_date < first_date or memory_date > today:
        return None
    return memory_date.isoformat()


def _build_daily_memory_counts(
    timestamps: Iterable[Any],
    *,
    today: date | None = None,
) -> list[dict[str, int | str]]:
    current_date = today or datetime.now(tz=UTC).date()
    first_date = current_date - timedelta(days=_TREND_DAYS - 1)
    counts = {
        (first_date + timedelta(days=offset)).isoformat(): 0
        for offset in range(_TREND_DAYS)
    }
    for value in timestamps:
        bucket = _recent_utc_date(value, today=current_date)
        if bucket is not None:
            counts[bucket] += 1
    return [{"date": bucket, "count": count} for bucket, count in counts.items()]


class StatsOperationsMixin:
    """统计信息、存储维护和图索引重建。"""

    async def count_canonical_created_on(self, day_ts: int) -> int:
        """统计指定 UTC 日（00:00 时间戳）写入的 canonical 记忆数量。

        Args:
            day_ts: 当天 00:00:00 的 UTC Unix 时间戳。

        Returns:
            该日创建的 canonical 记忆条数；数据库未初始化时返回 0。
        """

        if self.db_connection is None:
            return 0
        day_str = datetime.fromtimestamp(day_ts, tz=UTC).strftime("%Y-%m-%d")
        cursor = await self.db_connection.execute(
            "SELECT COUNT(*) FROM documents WHERE date(created_at) = ?",
            (day_str,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0] or 0) if row else 0

    async def get_session_memories(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """获取会话的所有记忆（分批处理 + 数据库排序优化）"""
        try:
            total_count = await self._faiss_db.document_storage.count_documents(
                metadata_filters={"session_id": session_id}
            )

            if total_count == 0:
                return []

            if total_count <= limit:
                all_docs = await self._faiss_db.document_storage.get_documents(
                    metadata_filters={"session_id": session_id},
                    limit=limit,
                    offset=0,
                )
                all_docs = await asyncio.to_thread(_normalize_batch_metadata, all_docs)
                sorted_docs = sorted(
                    all_docs,
                    key=lambda d: safe_float(
                        d.get("metadata", {}).get("create_time"), 0.0
                    ),
                    reverse=True,
                )
            else:
                all_docs = []
                batch_size = 500
                offset = 0

                while offset < total_count:
                    batch = await self._faiss_db.document_storage.get_documents(
                        metadata_filters={"session_id": session_id},
                        limit=batch_size,
                        offset=offset,
                    )
                    if not batch:
                        break
                    batch = await asyncio.to_thread(_normalize_batch_metadata, batch)
                    all_docs.extend(batch)
                    offset += batch_size

                sorted_docs = sorted(
                    all_docs,
                    key=lambda d: safe_float(
                        d.get("metadata", {}).get("create_time"), 0.0
                    ),
                    reverse=True,
                )[:limit]

            memories = []
            for doc in sorted_docs:
                memories.append(
                    {
                        "id": doc["id"],
                        "text": doc["text"],
                        "metadata": doc["metadata"],
                    }
                )
            return memories
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                f"[Maintenance] 获取会话记忆失败 (session_id={session_id})",
                exc_info=True,
            )
            return []

    async def get_statistics(self) -> dict[str, Any]:
        """获取记忆统计信息（批量处理避免内存问题）"""
        try:
            total_count = await self._faiss_db.document_storage.count_documents(
                metadata_filters={}
            )

            stats: dict[str, Any] = {"total_memories": total_count}

            session_counts: dict[str, int] = {}
            status_breakdown = {"active": 0, "archived": 0, "deleted": 0}
            importance_sum = 0.0
            importance_count = 0
            importance_distribution = {
                "0-1": 0,
                "1-2": 0,
                "2-3": 0,
                "3-4": 0,
                "4-5": 0,
                "5-6": 0,
                "6-7": 0,
                "7-8": 0,
                "8-9": 0,
                "9-10": 0,
            }
            oldest_time = None
            newest_time = None
            trend_today = datetime.now(tz=UTC).date()
            daily_memory_counts = _build_daily_memory_counts([], today=trend_today)
            daily_memory_by_date = {
                str(item["date"]): item for item in daily_memory_counts
            }

            batch_size = 500
            offset = 0

            while offset < total_count:
                batch_docs = await self._faiss_db.document_storage.get_documents(
                    metadata_filters={}, limit=batch_size, offset=offset
                )
                if not batch_docs:
                    break

                batch_docs = await asyncio.to_thread(
                    _normalize_batch_metadata, batch_docs
                )

                for doc in batch_docs:
                    metadata = doc["metadata"]

                    session_id = metadata.get("session_id")
                    if session_id:
                        session_counts[session_id] = (
                            session_counts.get(session_id, 0) + 1
                        )

                    status = metadata.get("status", "active")
                    if status in status_breakdown:
                        status_breakdown[status] += 1
                    else:
                        status_breakdown["active"] += 1

                    importance = metadata.get("importance")
                    if importance is not None:
                        clamped = clamp_float(importance, default=0.5)
                        importance_sum += clamped
                        importance_count += 1
                        display_importance = clamped * 10 if clamped <= 1 else clamped
                        bucket_idx = min(9, max(0, int(display_importance)))
                        bucket_keys = [
                            "0-1",
                            "1-2",
                            "2-3",
                            "3-4",
                            "4-5",
                            "5-6",
                            "6-7",
                            "7-8",
                            "8-9",
                            "9-10",
                        ]
                        importance_distribution[bucket_keys[bucket_idx]] += 1

                    create_time = metadata.get("create_time")
                    if create_time:
                        trend_bucket = _recent_utc_date(
                            create_time,
                            today=trend_today,
                        )
                        if trend_bucket is not None:
                            daily_memory_by_date[trend_bucket]["count"] += 1
                        create_time = safe_float(create_time, 0.0)
                        if oldest_time is None or create_time < oldest_time:
                            oldest_time = create_time
                        if newest_time is None or create_time > newest_time:
                            newest_time = create_time

                offset += batch_size

            stats["sessions"] = session_counts
            stats["status_breakdown"] = status_breakdown
            stats["avg_importance"] = (
                importance_sum / importance_count if importance_count > 0 else 0.0
            )
            stats["importance_distribution"] = importance_distribution
            stats["oldest_memory"] = oldest_time
            stats["newest_memory"] = newest_time
            stats["daily_memory_counts"] = daily_memory_counts
            if self._graph_store is not None:
                stats.update(await self._graph_store.get_memory_entry_stats())
                stats["graph_memory_enabled"] = True
            else:
                stats["graph_memory_enabled"] = False

            return stats
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}", exc_info=True)
            return {
                "total_memories": 0,
                "sessions": {},
                "status_breakdown": {"active": 0, "archived": 0, "deleted": 0},
                "avg_importance": 0.0,
                "oldest_memory": None,
                "newest_memory": None,
                "daily_memory_counts": _build_daily_memory_counts([]),
                "graph_memory_enabled": bool(self._graph_store is not None),
            }

    async def maintain_storage(self, *, vacuum: bool = False) -> dict[str, Any]:
        """执行 SQLite 存储维护并返回大小诊断。"""
        try:
            db_path = Path(self._db_path)
            wal_path = Path(f"{self._db_path}-wal")
            before_size = db_path.stat().st_size if db_path.exists() else 0
            before_wal_size = wal_path.stat().st_size if wal_path.exists() else 0

            if self._db is None:
                return {
                    "success": False,
                    "error": "database connection is not initialized",
                }

            async def _close_cursor(cursor: Any) -> None:
                close = getattr(cursor, "close", None)
                if not callable(close):
                    return
                maybe_awaitable = close()
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable

            fts_optimized: list[str] = []
            fts_skipped: dict[str, str] = {}
            for fts_table, optimize_sql in (
                (
                    MEMORY_FTS_TABLE,
                    MEMORY_FTS_OPTIMIZE_SQL,
                ),
                (
                    "memora_graph_entries_fts",
                    "INSERT INTO memora_graph_entries_fts(memora_graph_entries_fts) "
                    "VALUES ('optimize')",
                ),
                (
                    "memory_atoms_fts",
                    "INSERT INTO memory_atoms_fts(memory_atoms_fts) VALUES ('optimize')",
                ),
            ):
                try:
                    cursor = await self._db.execute(optimize_sql)
                    await _close_cursor(cursor)
                    fts_optimized.append(fts_table)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    fts_skipped[fts_table] = str(exc)
                    logger.debug(
                        f"[StorageMaintenance] 跳过 FTS optimize: {fts_table}",
                        exc_info=True,
                    )

            await self._db.commit()
            checkpoint_cursor = await self._db.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            )
            checkpoint_row = await checkpoint_cursor.fetchone()
            await _close_cursor(checkpoint_cursor)
            checkpoint = {
                "mode": "TRUNCATE",
                "busy": int(checkpoint_row[0]) if checkpoint_row else 0,
                "log_frames": int(checkpoint_row[1]) if checkpoint_row else 0,
                "checkpointed_frames": int(checkpoint_row[2]) if checkpoint_row else 0,
            }

            if vacuum:
                vacuum_cursor = await self._db.execute("VACUUM")
                await _close_cursor(vacuum_cursor)
                await self._db.commit()

            after_size = db_path.stat().st_size if db_path.exists() else 0
            after_wal_size = wal_path.stat().st_size if wal_path.exists() else 0
            return {
                "success": True,
                "vacuum": vacuum,
                "fts_optimized": fts_optimized,
                "fts_skipped": fts_skipped,
                "wal_checkpoint": checkpoint,
                "db_size_before": before_size,
                "db_size_after": after_size,
                "wal_size_before": before_wal_size,
                "wal_size_after": after_wal_size,
                "bytes_reclaimed": max(
                    0,
                    before_size + before_wal_size - after_size - after_wal_size,
                ),
            }
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[StorageMaintenance] 执行存储维护失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def rebuild_graph_index(self) -> dict[str, int]:
        """从存储的文档重建图记忆工件。"""
        if self._graph_memory_manager is None:
            return {"rebuilt": 0, "skipped": 0}

        total_count = await self._faiss_db.document_storage.count_documents(
            metadata_filters={}
        )
        batch_size = 200
        offset = 0
        rebuilt = 0
        skipped = 0

        while offset < total_count:
            docs = await self._faiss_db.document_storage.get_documents(
                metadata_filters={},
                limit=batch_size,
                offset=offset,
            )
            if not docs:
                break

            for doc in docs:
                metadata = doc.get("metadata") or {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                elif not isinstance(metadata, dict):
                    metadata = {}
                content = str(doc.get("text") or "")
                if not content.strip():
                    skipped += 1
                    continue
                await self._graph_memory_manager.index_memory(
                    doc["id"], content, metadata
                )
                rebuilt += 1

            offset += batch_size

        if self._invalidate_cache:
            self._invalidate_cache()
        return {"rebuilt": rebuilt, "skipped": skipped}
