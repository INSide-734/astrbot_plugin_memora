"""
记忆生命周期操作 Mixin
提供三阶段分层遗忘和会话数据迁移。

作为 Mixin 类使用，需要宿主类在 __init__ 中设置:
- self._config: dict
- self._db: 数据库连接
- self._faiss_db: FAISS 数据库
- self._batch_delete_memories: 批量删除回调
"""

import asyncio
import json
import time

from astrbot.api import logger

from ....shared.memory_status import effective_memory_status, set_memory_status
from ....shared.number_utils import clamp_float, safe_float
from ...decay.application.operations import _normalize_batch_metadata
from .write_coordinator import ConnectionRegistry, check_db_alive, is_connection_fatal


class LifecycleOperationsMixin:
    """提供三阶段分层遗忘与会话迁移能力。"""

    async def cleanup_old_memories(
        self,
        days_threshold: int | None = None,
        importance_threshold: float | None = None,
    ) -> int:
        """三阶段分层遗忘：ACTIVE → DORMANT → ARCHIVED → 物理删除。"""
        days = (
            self._config.get("cleanup_days_threshold", 30)
            if days_threshold is None
            else days_threshold
        )
        importance = (
            self._config.get("cleanup_importance_threshold", 0.3)
            if importance_threshold is None
            else importance_threshold
        )
        try:
            days = int(days)
            importance = float(importance)
        except (TypeError, ValueError):
            logger.error(
                f"清理参数格式错误: days_threshold={days}, importance_threshold={importance}"
            )
            return 0

        if days < 0:
            logger.error(f"清理参数无效: days_threshold={days}（必须大于等于 0）")
            return 0

        now = time.time()
        stage1_cutoff = now - days * 86400
        stage2_cutoff = now - (days * 2) * 86400
        stage3_cutoff = now - (days * 3) * 86400

        try:
            total_count = await self._faiss_db.document_storage.count_documents(
                metadata_filters={}
            )
            if total_count == 0:
                return 0

            batch_size = 500
            offset = 0
            to_dormant_ids: list[int] = []
            to_archive_ids: list[int] = []
            to_delete_ids: list[int] = []

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
                    create_time = safe_float(metadata.get("create_time"), time.time())
                    doc_importance = clamp_float(
                        metadata.get("importance"), default=0.5
                    )
                    mem_status = effective_memory_status(metadata)
                    status_changed_at = safe_float(
                        metadata.get("status_changed_at"), create_time
                    )

                    if mem_status == "archived" and status_changed_at < stage3_cutoff:
                        to_delete_ids.append(doc["id"])
                    elif mem_status == "dormant" and status_changed_at < stage2_cutoff:
                        to_archive_ids.append(doc["id"])
                    elif (
                        mem_status in ("active", None, "")
                        and create_time < stage1_cutoff
                        and doc_importance < importance
                    ):
                        to_dormant_ids.append(doc["id"])

                offset += len(batch_docs)
                if len(batch_docs) < batch_size:
                    break

            dormant_count = 0
            if to_dormant_ids:
                dormant_count = await self._batch_update_status(
                    to_dormant_ids, "dormant", now
                )
                logger.info(f"[清理] 第 1 阶段：{dormant_count} 条 → 休眠")

            archived_count = 0
            if to_archive_ids:
                archived_count = await self._batch_update_status(
                    to_archive_ids, "archived", now
                )
                logger.info(f"[清理] 第 2 阶段：{archived_count} 条 → 归档")

            deleted_count = 0
            if to_delete_ids and self._batch_delete_memories:
                deleted_count = await self._batch_delete_memories(to_delete_ids)
                logger.info(f"[清理] 第 3 阶段：{deleted_count} 条已物理删除")

            return dormant_count + archived_count + deleted_count
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[清理] 分层遗忘清理失败", exc_info=True)
            return 0

    async def _batch_update_status(
        self, memory_ids: list[int], new_status: str, timestamp: float
    ) -> int:
        """批量同步更新生命周期状态和兼容字段，并应用情感衰减。"""
        if not memory_ids or self._db is None:
            return 0

        updated = 0
        for mem_id in memory_ids:
            try:
                cursor = await self._db.execute(
                    "SELECT metadata FROM documents WHERE id = ?", (mem_id,)
                )
                row = await cursor.fetchone()
                if not row:
                    continue
                metadata_str = row[0] if row[0] else "{}"
                metadata = (
                    json.loads(metadata_str) if isinstance(metadata_str, str) else {}
                )
                if not isinstance(metadata, dict):
                    metadata = {}
                set_memory_status(
                    metadata,
                    new_status,
                    status_changed_at=timestamp,
                )

                if new_status == "dormant":
                    if "emotion_tags" in metadata:
                        metadata["_archived_emotion_tags"] = metadata["emotion_tags"]
                    metadata["emotional_intensity"] = round(
                        float(metadata.get("emotional_intensity", 0.5)) * 0.5, 3
                    )
                elif new_status == "archived":
                    metadata["emotion_tags"] = []
                    metadata["emotional_intensity"] = 0.0

                await self._db.execute(
                    "UPDATE documents SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), timestamp, mem_id),
                )
                updated += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(f"[维护] 状态更新失败 (id={mem_id})", exc_info=True)
        if updated:
            await self._db.commit()
            if self._invalidate_cache:
                self._invalidate_cache()
        return updated

    async def migrate_session_if_needed(self, unified_msg_origin: str) -> None:
        """运行时自动迁移：将旧格式 session_id 更新为 unified_msg_origin 格式。"""
        try:
            parts = unified_msg_origin.split(":", 2)
            if len(parts) != 3:
                logger.warning(
                    f"[自动迁移] unified_msg_origin 格式不正确: {unified_msg_origin}"
                )
                return

            _platform_id, _message_type, full_session_id = parts

            candidates = [full_session_id]
            if "!" in full_session_id:
                parts_by_bang = full_session_id.split("!")
                for i in range(1, len(parts_by_bang)):
                    candidates.append("!".join(parts_by_bang[i:]))

            logger.info(f"[自动迁移] 开始检查会话，候选匹配: {candidates}")

            migration_key = f"migrated_umo_{unified_msg_origin}"
            if self._db is None:
                return
            if not check_db_alive(self._db):
                logger.warning("[迁移] 数据库连接坏死，尝试自动重连……")
                if not await ConnectionRegistry.try_repair():
                    logger.error("[迁移] 自动重连失败，跳过迁移")
                    return
            cursor = await self._db.execute(
                "SELECT value FROM migration_status WHERE key = ?", (migration_key,)
            )
            row = await cursor.fetchone()
            if row and row[0] == "true":
                return

            query = """
                SELECT id, metadata FROM documents
                WHERE json_extract(metadata, '$.session_id') IN (
                    SELECT value FROM json_each(:candidates_json)
                )
                AND json_extract(metadata, '$.session_id') NOT LIKE '%:%'
            """

            cursor = await self._db.execute(
                query,
                {"candidates_json": json.dumps(candidates)},
            )
            rows = list(await cursor.fetchall())

            if not rows:
                logger.info("[自动迁移] 未找到需要迁移的旧数据")
                await self._db.execute(
                    "INSERT OR REPLACE INTO migration_status (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (migration_key, "true"),
                )
                await self._db.commit()
                return

            logger.info(f"[自动迁移] 找到 {len(list(rows))} 条旧数据需要迁移")

            updated_count = 0
            for row in rows:
                doc_id = row[0]
                metadata_str = row[1]

                try:
                    metadata = json.loads(metadata_str) if metadata_str else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                old_session_id = metadata.get("session_id", "unknown")
                metadata["session_id"] = unified_msg_origin
                metadata["migrated_at"] = time.time()
                metadata["old_session_id"] = old_session_id

                await self._db.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), doc_id),
                )
                updated_count += 1

            await self._db.commit()
            await self._db.execute(
                "INSERT OR REPLACE INTO migration_status (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                (migration_key, "true"),
            )
            await self._db.commit()

            logger.info(
                f"[自动迁移] 完成，已将 {updated_count} 条记录更新为 {unified_msg_origin}"
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_connection_fatal(exc):
                logger.error(f"[自动迁移] 连接坏死，迁移中止: {exc}")
            else:
                logger.error(f"[自动迁移] 迁移失败: {exc}", exc_info=True)
