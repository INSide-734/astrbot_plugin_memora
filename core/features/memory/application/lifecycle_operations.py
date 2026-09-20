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
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from astrbot.api import logger

from ....shared.memory_status import effective_memory_status, set_memory_status
from ....shared.number_utils import clamp_float, safe_float
from ....shared.temporal import parse_datetime, serialize_datetime
from ...decay.application.operations import _normalize_batch_metadata
from .write_coordinator import ConnectionRegistry, check_db_alive, is_connection_fatal


def _safe_count(value: Any) -> int:
    """把派生端口报告的计数规范化为非负整数；布尔与非整数按 0 处理。"""

    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _log_derived_invalidation(summary: dict[str, Any], new_status: str) -> None:
    """把状态批更新的派生失效摘要落成稳定原因码与计数。

    派生失效失败不得只剩各阶段各自的 warning：维护调用方（``cleanup_old_memories``
    返回整数计数，保持兼容）需要一条可观测的汇总。稳定原因码只表达失败类别，
    日志不含 memory id、正文、scope/privacy/revision 取值；全部收敛时不写日志。
    """

    steps_failed = _safe_count(summary.get("steps_failed"))
    if not steps_failed:
        return
    logger.warning(
        "[维护] 状态派生失效未全部收敛："
        "reason_code=derived_invalidation_failed"
        f"，new_status={new_status}"
        f"，steps_failed={steps_failed}"
        f"，sources={_safe_count(summary.get('sources'))}"
        f"，evolution_invalidated={_safe_count(summary.get('evolution_invalidated'))}"
        f"，graph_cleaned={_safe_count(summary.get('graph_cleaned'))}"
        f"，graph_failed={_safe_count(summary.get('graph_failed'))}"
        f"，atoms_rederived={_safe_count(summary.get('atoms_rederived'))}"
        f"，atoms_failed={_safe_count(summary.get('atoms_failed'))}"
        f"，atoms_reason_code={summary.get('atoms_reason_code') or 'none'}"
    )


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
        """批量同步更新生命周期状态和兼容字段，并应用情感衰减。

        ``documents.updated_at`` 是 revision 与存储后端按 ``datetime`` 解析的
        时间列，必须写成 ISO 8601 文本：与 ``vector_retriever`` /
        ``write_op_repair`` 等写入方同格式，写 Unix 秒会让存储后端解析整批
        读取时抛错。失败不抛出、不改语义：状态写失败只跳过该行，派生失效
        失败按 ``_invalidate_derived_after_status_change`` 的摘要记录原因码。
        """
        if not memory_ids or self._db is None:
            return 0

        updated_at = serialize_datetime(parse_datetime(timestamp))
        if not updated_at:
            logger.warning(
                "[维护] 状态批更新跳过："
                f"reason_code=status_update_timestamp_invalid，new_status={new_status}"
            )
            return 0

        updated_ids: list[int] = []
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
                        safe_float(metadata.get("emotional_intensity"), 0.5) * 0.5, 3
                    )
                elif new_status == "archived":
                    metadata["emotion_tags"] = []
                    metadata["emotional_intensity"] = 0.0

                await self._db.execute(
                    "UPDATE documents SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), updated_at, mem_id),
                )
                updated_ids.append(mem_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(f"[维护] 状态更新失败 (id={mem_id})", exc_info=True)
        if updated_ids:
            await self._db.commit()
            if self._invalidate_cache:
                self._invalidate_cache()
            summary = await self._invalidate_derived_after_status_change(
                updated_ids, new_status
            )
            _log_derived_invalidation(summary, new_status)
        return len(updated_ids)

    async def _invalidate_derived_after_status_change(
        self, memory_ids: list[int], new_status: str
    ) -> dict[str, Any]:
        """状态批更新提交后统一失效派生面，返回安全计数。

        canonical 行已经提交，这里只处理可重建派生数据：evolution relation/projection
        按新 revision 失效、graph 源级残留回收、Atom 重派生（固定签名
        ``rederive_for_sources(ids, reason)``，实现缺失时按 debug 跳过）。catalog 的
        dirty 登记由 canonical ``UPDATE`` 触发器在同一事务写入，不在此重复登记。
        任一步普通失败只记降级计数与固定原因码，不回滚状态写；Atom 重派生失败按
        manager 报告如实透出（``atoms_failed``/``atoms_reason_code``），持久收敛由
        atoms 重建阶段承担；``asyncio.CancelledError`` 继续传播。
        """

        normalized_ids = sorted({int(memory_id) for memory_id in memory_ids})
        if not normalized_ids:
            return {
                "sources": 0,
                "evolution_invalidated": 0,
                "graph_cleaned": 0,
                "graph_failed": 0,
                "atoms_rederived": 0,
                "atoms_failed": 0,
                "atoms_reason_code": None,
                "steps_failed": 0,
            }

        engine = self._resolve_memory_engine_owner()
        invalidated, evolution_ok = await self._invalidate_evolution_sources(
            normalized_ids, engine
        )
        graph_cleaned, graph_ok = await self._reap_graph_residue(normalized_ids, engine)
        (
            atoms_rederived,
            atoms_failed,
            atoms_reason_code,
        ) = await self._rederive_atoms_for_sources(normalized_ids, new_status, engine)
        return {
            "sources": len(normalized_ids),
            "evolution_invalidated": invalidated,
            "graph_cleaned": graph_cleaned,
            "graph_failed": 0 if graph_ok else len(normalized_ids),
            "atoms_rederived": atoms_rederived,
            "atoms_failed": atoms_failed,
            "atoms_reason_code": atoms_reason_code,
            "steps_failed": sum(
                1 for ok in (evolution_ok, graph_ok, atoms_failed == 0) if not ok
            ),
        }

    def _resolve_memory_engine_owner(self) -> Any | None:
        """从已注入的绑定回调恢复宿主引擎，用于解析派生失效端口。

        ``MaintenanceOperations`` 只持有回调对象，宿主引擎是这些绑定回调的
        ``__self__``；与 reconsolidation 的既有权属解析保持同一做法。
        """

        for attr in ("_update_memory", "_batch_delete_memories"):
            callback = getattr(self, attr, None)
            owner = getattr(callback, "__self__", None)
            if owner is not None:
                return owner
        return None

    def _resolve_derived_port(self, name: str, engine: Any | None) -> Any | None:
        """解析派生面端口：宿主自身属性优先，其次绑定回调所属的引擎。"""

        port = getattr(self, name, None)
        if port is not None:
            return port
        return getattr(engine, name, None) if engine is not None else None

    async def _invalidate_evolution_sources(
        self, memory_ids: list[int], engine: Any | None
    ) -> tuple[int, bool]:
        """按当前 revision 失效 relation/projection；失败只降级计数。"""

        store = self._resolve_derived_port("memory_evolution_store", engine)
        invalidate = getattr(store, "invalidate_for_source_revision", None)
        load_sources = getattr(store, "load_sources", None)
        if not callable(invalidate) or not callable(load_sources):
            logger.debug(
                "[维护] evolution 派生失效不可用，"
                "reason_code=evolution_invalidate_unavailable"
            )
            return 0, True
        invalidate_revision = cast(Callable[[int, str], Awaitable[Any]], invalidate)
        load_source_rows = cast(Callable[..., Awaitable[list[Any]]], load_sources)
        invalidated = 0
        try:
            for memory_id in memory_ids:
                sources = await load_source_rows((memory_id,), active_only=False)
                if not sources:
                    continue
                revision_token = str(getattr(sources[0], "revision_token", "") or "")
                if not revision_token:
                    continue
                invalidated += max(
                    0, int(await invalidate_revision(memory_id, revision_token) or 0)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "[维护] evolution 派生失效降级，reason_code=evolution_invalidate_failed"
            )
            return invalidated, False
        return invalidated, True

    async def _reap_graph_residue(
        self, memory_ids: list[int], engine: Any | None
    ) -> tuple[int, bool]:
        """回收不再可召回来源的图行列；失败只降级计数。"""

        manager = self._resolve_derived_port("graph_memory_manager", engine)
        batch_delete = getattr(manager, "batch_delete_memories", None)
        if not callable(batch_delete):
            logger.debug(
                "[维护] graph 源级残留回收不可用，"
                "reason_code=graph_residue_cleanup_unavailable"
            )
            return 0, True
        delete_residue = cast(Callable[[list[int]], Awaitable[None]], batch_delete)
        try:
            await delete_residue(list(memory_ids))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "[维护] graph 源级残留回收降级，"
                "reason_code=graph_residue_cleanup_failed"
            )
            return 0, False
        return len(memory_ids), True

    async def _rederive_atoms_for_sources(
        self, memory_ids: list[int], new_status: str, engine: Any | None
    ) -> tuple[int, int, str | None]:
        """按固定签名重派生 Atom；实现缺失时只记 debug 日志。

        返回 ``(已收敛来源数, 失败来源数, 稳定原因码)``。manager 报告
        ``failed``/``needs_repair`` 时必须如实透出，不得在报告失败时返回成功语义：
        ``atoms_rederived`` 只计 ``rederived + purged``（父已不可召回而清除 Atom 行
        同样属于收敛成功），失败来源数交给状态批更新结果计入 ``steps_failed``。
        失败只降级、不回滚状态写；持久收敛由 atoms 重建阶段承担。
        """

        manager = self._resolve_derived_port("atom_lifecycle_manager", engine)
        rederive = getattr(manager, "rederive_for_sources", None)
        if not callable(rederive):
            logger.debug(
                "[维护] Atom 重派生不可用，reason_code=atom_rederive_unavailable"
            )
            return 0, 0, None
        try:
            result = rederive(list(memory_ids), f"decay_{new_status}")
            if inspect.isawaitable(result):
                result = await cast(Awaitable[Any], result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[维护] Atom 重派生降级，reason_code=atom_rederive_failed")
            return 0, len(memory_ids), "atom_rederive_failed"
        if not isinstance(result, dict):
            # 无报告的旧实现：按请求来源数计成功，保持既有调用形状。
            return len(memory_ids), 0, None
        rederived = _safe_count(result.get("rederived")) + _safe_count(
            result.get("purged")
        )
        failed = _safe_count(result.get("failed"))
        if not failed and result.get("needs_repair"):
            # 报告只给 needs_repair 时按「至少一个来源待修复」计入，不当成功。
            failed = 1
        if failed:
            logger.warning(
                "[维护] Atom 重派生部分失败，reason_code=atom_rederive_failed"
            )
            return rederived, failed, "atom_rederive_failed"
        return rederived, 0, None

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
