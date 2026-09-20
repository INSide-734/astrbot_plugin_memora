"""控制台页面的维护操作接口（重建、清理、压缩、备份）。"""

from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any, cast

import aiosqlite
from astrbot.api import logger

from ....features.memory.infrastructure.base import apply_perf_pragmas
from ....features.memory.infrastructure.validators import PersistenceHealthValidator
from ....shared.sql import MEMORY_FTS_TABLE
from .backup_api import BackupApiMixin
from .response_utils import error_response, ok_response

# 允许清理的孤儿目标来自 PersistenceHealthValidator 的只读报告。
_ORPHAN_REPAIR_TARGETS: tuple[str, ...] = (
    "orphan_bm25_doc_ids",
    "orphan_main_vector_ids",
    "orphan_graph_vector_ids",
)
_ORPHAN_REPORT_COUNT_KEYS: dict[str, str] = {
    "orphan_bm25_doc_ids": "bm25",
    "orphan_main_vector_ids": "main_vectors",
    "orphan_graph_vector_ids": "graph_vectors",
}
_ORPHAN_DELETE_BATCH_SIZE = 200


def _batched_orphan_ids(orphan_ids: list[int]) -> Iterator[list[int]]:
    """按固定批量切分孤儿候选，避免单条语句参数过多。"""

    for offset in range(0, len(orphan_ids), _ORPHAN_DELETE_BATCH_SIZE):
        yield orphan_ids[offset : offset + _ORPHAN_DELETE_BATCH_SIZE]


class MaintenanceApiMixin:
    _dashboard_runtime_lock: asyncio.Lock | None = None

    @staticmethod
    def _maintenance_json_object_payload_or_error(payload):
        if isinstance(payload, dict):
            return payload, None
        return None, error_response("请求体必须为 JSON 对象")

    @staticmethod
    def _coerce_result_int(value, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_result_str_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _coerce_result_list(value):
        if isinstance(value, list):
            return value
        if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
            return []
        try:
            return list(value)
        except TypeError:
            return []

    @staticmethod
    def _coerce_config_bool(value) -> bool:
        """解析配置布尔值，避免任意非空字符串都被视为真。"""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            return False
        return bool(value)

    def _get_dashboard_runtime_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_dashboard_runtime_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._dashboard_runtime_lock = lock
        return lock

    def _dashboard_runtime_config(self) -> tuple[bool, int, int]:
        config_manager = getattr(getattr(self, "plugin", None), "config_manager", None)
        if config_manager is None:
            return False, 120, 20000
        allow_runtime_build = MaintenanceApiMixin._coerce_config_bool(
            config_manager.get("dashboard.allow_runtime_build", False)
        )
        try:
            timeout_seconds = int(
                config_manager.get("dashboard.build_timeout_seconds", 120)
            )
        except (TypeError, ValueError):
            timeout_seconds = 120
        try:
            max_output_chars = int(
                config_manager.get("dashboard.max_output_chars", 20000)
            )
        except (TypeError, ValueError):
            max_output_chars = 20000
        return (
            allow_runtime_build,
            max(5, timeout_seconds),
            max(1000, max_output_chars),
        )

    @staticmethod
    def _truncate_command_output(content: str, max_chars: int) -> str:
        if len(content) <= max_chars:
            return content
        suffix = "\n...[输出已截断]..."
        if max_chars <= len(suffix):
            return content[:max_chars]
        return content[: max_chars - len(suffix)] + suffix

    def _dashboard_runtime_build_disabled_response(self) -> dict:
        return error_response(
            "控制台页面运行时构建/安装能力已禁用；如需启用，请将 dashboard.allow_runtime_build 设为 true"
        )

    @staticmethod
    def _resolve_command_executable(command: str) -> str:
        resolved = shutil.which(command)
        if resolved:
            return resolved

        if sys.platform.startswith("win"):
            for suffix in (".cmd", ".exe", ".bat"):
                resolved = shutil.which(f"{command}{suffix}")
                if resolved:
                    return resolved

        raise FileNotFoundError(f"未找到可执行命令：{command}")

    async def rebuild_index(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        try:
            initializer = getattr(self.plugin, "initializer", None)
            validator = getattr(initializer, "index_validator", None)
            if validator is None:
                return error_response("索引校验器不可用")
            started_at = time.perf_counter()
            coordinator = self._resolve_rebuild_coordinator(initializer, validator)
            if coordinator is not None:
                report = await coordinator.rebuild_stages(["indexes"])
                result = coordinator.stage_result(report, "indexes")
            else:
                logger.debug(
                    "索引重建未走统一入口，reason_code=rebuild_coordinator_unavailable"
                )
                result = await validator.rebuild_indexes(engine)
            self._record_index_rebuild_observability(
                result,
                time.perf_counter() - started_at,
            )
            return ok_response({"message": "索引重建完成", "result": result})
        except Exception as e:
            elapsed = (
                time.perf_counter() - started_at if "started_at" in locals() else 0.0
            )
            self._record_index_rebuild_observability({}, elapsed, error=e)
            logger.error(f"索引重建失败: {e}")
            return error_response(f"索引重建失败：{e}")

    @staticmethod
    def _resolve_rebuild_coordinator(initializer, validator):
        """解析统一重建入口，缺失时调用方记录豁免原因并保留既有直连路径。

        transport 不依赖组合根：初始化器发布端口 ``derived_rebuild_coordinator``，
        协调器构造时也把自身登记在同一组合持有的验证器上；这里只做结构性检查——
        端口对象必须暴露 coroutine 入口 ``rebuild_stages``。
        """

        for owner in (initializer, validator):
            entry = getattr(owner, "derived_rebuild_coordinator", None)
            if inspect.iscoroutinefunction(getattr(entry, "rebuild_stages", None)):
                return entry
        return None

    def _record_index_rebuild_observability(
        self,
        result: dict,
        duration_seconds: float,
        *,
        error: Exception | None = None,
    ) -> None:
        if not isinstance(result, dict):
            result = {}
        success = bool(result.get("success", False)) if error is None else False
        errors_default = 0 if success else 1
        message = result.get("message")
        if error is not None:
            message = str(error)
        snapshot = {
            "last_rebuild_success": success,
            "last_rebuild_duration_seconds": max(0.0, float(duration_seconds)),
            "last_rebuild_errors": MaintenanceApiMixin._coerce_result_int(
                result.get("errors", errors_default),
                errors_default,
            ),
            "last_rebuild_total": MaintenanceApiMixin._coerce_result_int(
                result.get("total", 0),
                0,
            ),
            "last_rebuild_message": str(message or ""),
            "last_rebuild_at": time.time(),
        }
        plugin = getattr(self, "plugin", None)
        if plugin is not None:
            setattr(plugin, "_index_observability", snapshot)
            initializer = getattr(plugin, "initializer", None)
            if initializer is not None:
                setattr(initializer, "_index_observability", snapshot)

    async def rebuild_graph_index(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        try:
            initializer = getattr(self.plugin, "initializer", None)
            validator = getattr(initializer, "index_validator", None)
            coordinator = self._resolve_rebuild_coordinator(initializer, validator)
            if coordinator is not None:
                report = await coordinator.rebuild_stages(["graph"])
                result = coordinator.stage_result(report, "graph")
            else:
                if not hasattr(engine, "rebuild_graph_index"):
                    return error_response("图索引重建能力不可用")
                logger.debug(
                    "图索引重建未走统一入口，"
                    "reason_code=rebuild_coordinator_unavailable"
                )
                result = await engine.rebuild_graph_index()
            return ok_response({"message": "图索引重建完成", "result": result})
        except Exception as e:
            logger.error(f"图索引重建失败: {e}")
            return error_response(f"图索引重建失败：{e}")

    async def get_persistence_health(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        try:
            initializer = getattr(self.plugin, "initializer", None)
            validator, error = MaintenanceApiMixin._persistence_health_validator(
                engine, initializer
            )
            if error:
                return error
            return ok_response(await validator.check())
        except Exception as e:
            logger.error(f"持久化健康检查失败: {e}", exc_info=True)
            return error_response(f"持久化健康检查失败：{e}")

    @staticmethod
    def _persistence_health_validator(engine, initializer) -> tuple[Any, Any]:
        """构造只读持久化健康检查器；数据库路径不可用时返回显式错误。"""

        index_validator = getattr(initializer, "index_validator", None)
        db_path = getattr(index_validator, "db_path", None) or getattr(
            engine, "db_path", None
        )
        if not db_path:
            return None, error_response("数据库路径不可用")
        graph_faiss_db = getattr(engine, "graph_vector_db", None)
        return (
            PersistenceHealthValidator(
                db_path,
                getattr(index_validator, "faiss_db", None)
                or getattr(engine, "faiss_db", None),
                graph_faiss_db,
            ),
            None,
        )

    @staticmethod
    def _coerce_orphan_ids(value) -> list[int]:
        """把健康报告中的孤儿 ID 列表归一为去重正整数。"""

        if not isinstance(value, list):
            return []
        normalized: set[int] = set()
        for item in value:
            if isinstance(item, bool):
                continue
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number > 0:
                normalized.add(number)
        return sorted(normalized)

    async def repair_persistence_health(self):
        """按显式目标清理 BM25/FAISS 孤儿索引行。

        候选来自 ``PersistenceHealthValidator`` 的只读报告；删除前重新核对当前
        canonical/图表状态，因此不会误删仍在使用的索引行（含 ID 复用竞态）。
        默认的「重建只补缺 ID」向量重建策略不变；清理失败返回显式错误。
        """
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        from quart import request

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._maintenance_json_object_payload_or_error(
            payload
        )
        if error:
            return error
        targets = payload.get("targets")
        if not isinstance(targets, list) or not targets:
            return error_response("repair requires explicit targets")
        selected: list[str] = []
        for target in targets:
            name = str(target)
            if name not in _ORPHAN_REPAIR_TARGETS:
                return error_response(
                    "持久化健康修复仅支持孤儿索引清理，可选目标："
                    + "、".join(_ORPHAN_REPAIR_TARGETS)
                )
            if name not in selected:
                selected.append(name)

        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        initializer = getattr(self.plugin, "initializer", None)
        validator, error = MaintenanceApiMixin._persistence_health_validator(
            engine, initializer
        )
        if error:
            return error
        try:
            report = await validator.check()
            issues = report.get("issues") if isinstance(report, dict) else None
            if not isinstance(issues, dict) or "check_failed" in issues:
                raise RuntimeError("persistence_health_check_failed")
            counts = report.get("counts", {})
            if not isinstance(counts, dict) or any(
                _ORPHAN_REPORT_COUNT_KEYS[target] not in counts for target in selected
            ):
                raise RuntimeError("orphan_target_unavailable")
            repaired: dict[str, int] = {}
            for target in selected:
                repaired[target] = await self._purge_orphan_index_rows(
                    target,
                    MaintenanceApiMixin._coerce_orphan_ids(issues.get(target)),
                    engine=engine,
                    initializer=initializer,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("持久化健康修复失败，异常类型=%s", error.__class__.__name__)
            return error_response("持久化健康修复失败")
        return ok_response({"message": "孤儿索引清理完成", "repaired": repaired})

    async def _purge_orphan_index_rows(
        self, target: str, orphan_ids: list[int], *, engine, initializer
    ) -> int:
        """按目标清理一类孤儿索引行；返回实际删除数量。"""

        index_validator = getattr(initializer, "index_validator", None)
        if target == "orphan_bm25_doc_ids":
            db_path = getattr(index_validator, "db_path", None) or getattr(
                engine, "db_path", None
            )
            if not db_path:
                raise RuntimeError("database_path_unavailable")
            if not orphan_ids:
                return 0
            return await MaintenanceApiMixin._delete_orphan_bm25_rows(
                db_path, orphan_ids
            )
        graph_manager = getattr(engine, "graph_memory_manager", None)
        graph_faiss_db = getattr(engine, "graph_vector_db", None)
        if target == "orphan_main_vector_ids":
            main_faiss_db = getattr(index_validator, "faiss_db", None) or getattr(
                engine, "faiss_db", None
            )
            if main_faiss_db is None:
                raise RuntimeError("main_vector_store_unavailable")
            if not orphan_ids:
                return 0
            return await MaintenanceApiMixin._delete_orphan_main_vector_ids(
                main_faiss_db, orphan_ids
            )
        if graph_faiss_db is None:
            raise RuntimeError("graph_vector_store_unavailable")
        graph_store = getattr(engine, "graph_store", None) or getattr(
            graph_manager, "graph_store", None
        )
        if graph_store is None or not callable(
            getattr(graph_store, "list_unreferenced_vector_doc_ids", None)
        ):
            raise RuntimeError("graph_store_unavailable")
        if not orphan_ids:
            return 0
        return await MaintenanceApiMixin._delete_orphan_graph_vector_ids(
            graph_faiss_db, graph_store, orphan_ids
        )

    @staticmethod
    async def _delete_orphan_bm25_rows(db_path: str, orphan_ids: list[int]) -> int:
        """删除已无 canonical 来源的 BM25 索引行，返回实际删除行数。

        SQL 在同一语句内以 ``documents`` 重新核对，避免并发写入后误删。
        """

        deleted = 0
        async with aiosqlite.connect(db_path) as db:
            await apply_perf_pragmas(db)
            for batch in _batched_orphan_ids(orphan_ids):
                placeholders = ", ".join("?" for _ in batch)
                cursor = await db.execute(
                    f"DELETE FROM {MEMORY_FTS_TABLE} "
                    f"WHERE doc_id IN ({placeholders}) "
                    "AND doc_id NOT IN (SELECT id FROM documents)",
                    batch,
                )
                deleted += max(0, int(cursor.rowcount or 0))
            await db.commit()
        return deleted

    @staticmethod
    def _parse_positive_int(value: Any) -> int | None:
        """解析向量文档行的整数 ID；无法证明时返回 None。"""
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    async def _delete_orphan_main_vector_ids(faiss_db, orphan_ids: list[int]) -> int:
        """删除主向量库中已无 canonical 文档的孤儿向量槽位。

        删除前按 canonical 文档行重新核对：文档行存在说明 ID 已被复用，跳过该候选。
        本操作不改变「重建只补缺 ID」的默认向量重建策略。
        """

        document_storage = getattr(faiss_db, "document_storage", None)
        embedding_storage = getattr(faiss_db, "embedding_storage", None)
        if document_storage is None or embedding_storage is None:
            raise RuntimeError("main_vector_store_unavailable")
        deleted = 0
        for batch in _batched_orphan_ids(orphan_ids):
            documents = await document_storage.get_documents(
                metadata_filters={}, ids=list(batch), limit=len(batch)
            )
            live_ids: set[int] = set()
            invalid_row = False
            for document in documents or ():
                if not isinstance(document, Mapping):
                    invalid_row = True
                    break
                document_id = MaintenanceApiMixin._parse_positive_int(
                    document.get("id")
                )
                if document_id is None:
                    invalid_row = True
                    break
                live_ids.add(document_id)
            if invalid_row:
                logger.debug(
                    "主向量孤儿清理跳过无法核对的批次，"
                    "reason_code=orphan_live_row_unparseable"
                )
                continue
            stale_ids = [item for item in batch if item not in live_ids]
            if stale_ids:
                result = await embedding_storage.delete(stale_ids)
                if result is False:
                    raise RuntimeError("main_vector_delete_failed")
                deleted += len(stale_ids)
        return deleted

    @staticmethod
    async def _delete_orphan_graph_vector_ids(
        graph_faiss_db, graph_store, orphan_ids: list[int]
    ) -> int:
        """删除图表未引用的图向量及其图文档行，返回实际删除数量。"""

        document_storage = getattr(graph_faiss_db, "document_storage", None)
        embedding_storage = getattr(graph_faiss_db, "embedding_storage", None)
        list_unreferenced = getattr(
            graph_store, "list_unreferenced_vector_doc_ids", None
        )
        if document_storage is None or embedding_storage is None:
            raise RuntimeError("graph_vector_store_unavailable")
        if not callable(list_unreferenced):
            raise RuntimeError("graph_store_unavailable")
        candidates = MaintenanceApiMixin._coerce_orphan_ids(
            await cast(Callable[[list[int]], Awaitable[list[int]]], list_unreferenced)(
                list(orphan_ids)
            )
        )
        deleted = 0
        for batch in _batched_orphan_ids(candidates):
            documents = await document_storage.get_documents(
                metadata_filters={}, ids=list(batch), limit=len(batch)
            )
            documented_rows: list[tuple[int, str]] = []
            invalid_row = False
            for document in documents or ():
                if not isinstance(document, Mapping):
                    invalid_row = True
                    break
                doc_uuid = document.get("doc_id")
                document_id = MaintenanceApiMixin._parse_positive_int(
                    document.get("id")
                )
                if not doc_uuid or document_id is None:
                    invalid_row = True
                    break
                documented_rows.append((document_id, str(doc_uuid)))
            if invalid_row:
                logger.debug(
                    "图向量孤儿清理跳过无法核对的批次，"
                    "reason_code=orphan_live_row_unparseable"
                )
                continue
            documented_ids = {document_id for document_id, _ in documented_rows}
            for _document_id, doc_uuid in documented_rows:
                result = await graph_faiss_db.delete(doc_uuid)
                if result is False:
                    raise RuntimeError("graph_vector_delete_failed")
                deleted += 1
            stale_ids = [item for item in batch if item not in documented_ids]
            if stale_ids:
                result = await embedding_storage.delete(stale_ids)
                if result is False:
                    raise RuntimeError("graph_vector_delete_failed")
                deleted += len(stale_ids)
        return deleted

    async def purge_deleted_memories(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        try:
            purged = 0
            if hasattr(engine, "maintenance") and hasattr(
                engine.maintenance, "purge_deleted"
            ):
                purged = await engine.maintenance.purge_deleted()
            return ok_response(
                {"purged": purged, "message": f"已清理 {purged} 条已删除记忆"}
            )
        except Exception as e:
            logger.error(f"清理已删除记忆失败: {e}")
            return error_response(f"清理已删除记忆失败：{e}")

    async def compact_database(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        try:
            if hasattr(engine, "db_connection") and engine.db_connection:
                await engine.db_connection.execute("VACUUM")
            return ok_response({"message": "数据库压缩完成"})
        except Exception as e:
            logger.error(f"压缩数据库失败: {e}")
            return error_response(f"压缩数据库失败：{e}")

    create_backup = BackupApiMixin.create_backup
    list_backups = BackupApiMixin.list_backups
    delete_backup = BackupApiMixin.delete_backup
    batch_delete_backups = BackupApiMixin.batch_delete_backups
    restore_backup = BackupApiMixin.restore_backup
    get_backup_status = BackupApiMixin.get_backup_status
    cancel_restore = BackupApiMixin.cancel_restore

    async def export_memories(self):
        """导出记忆为 JSONL 或 Markdown（返回内联内容用于浏览器下载）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._maintenance_json_object_payload_or_error(
            payload
        )
        if error:
            return error
        export_format = str(payload.get("format", "jsonl")).strip().lower()
        _date_from = str(payload.get("date_from", "")).strip() or None
        _date_to = str(payload.get("date_to", "")).strip() or None

        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]

        exporter = getattr(engine, "memory_exporter", None)
        if exporter is None:
            return error_response("记忆导出器不可用")

        try:
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=f".{export_format}", delete=False, encoding="utf-8"
            ) as tmp:
                tmp_path = tmp.name

            try:
                if export_format == "markdown":
                    count = await exporter.export_markdown(tmp_path)
                else:
                    count = await exporter.export_jsonl(tmp_path)
                count = MaintenanceApiMixin._coerce_result_int(count, 0)

                with open(tmp_path, encoding="utf-8") as f:
                    content = f.read()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning("清理记忆导出临时文件失败")

            return ok_response(
                {
                    "content": content,
                    "count": count,
                    "format": export_format,
                }
            )
        except Exception as e:
            logger.error("导出记忆失败，异常类型=%s", e.__class__.__name__)
            return error_response("导出记忆失败")

    # ---- Dashboard 管理（npm install / build） ----

    async def _run_npm_command(
        self,
        args: list[str],
        cwd: str,
        *,
        timeout_seconds: int,
        max_output_chars: int,
    ) -> dict:
        """在指定目录执行 npm 命令，并返回执行结果字典。"""
        # 先检查 Node.js 是否可用
        try:
            resolved_node = self._resolve_command_executable("node")
            node_check = await asyncio.create_subprocess_exec(
                resolved_node,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, _stderr = await asyncio.wait_for(
                node_check.communicate(),
                timeout=10,
            )
            if node_check.returncode != 0:
                return {
                    "stdout": "",
                    "stderr": "Node.js 不可用，请先安装 Node.js。",
                    "exit_code": -1,
                    "success": False,
                }
        except FileNotFoundError:
            return {
                "stdout": "",
                "stderr": "未找到 Node.js，请先安装 Node.js。",
                "exit_code": -1,
                "success": False,
            }
        except Exception as e:
            logger.error(f"Node.js 检查失败: {e}")
            return {
                "stdout": "",
                "stderr": f"Node.js 检查失败：{e}",
                "exit_code": -1,
                "success": False,
            }

        try:
            resolved_args = [
                self._resolve_command_executable(args[0]),
                *args[1:],
            ]
            proc = await asyncio.create_subprocess_exec(
                *resolved_args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                proc.kill()
                stdout_bytes, stderr_bytes = await proc.communicate()
                stdout = (
                    stdout_bytes.decode("utf-8", errors="replace")
                    if stdout_bytes
                    else ""
                )
                stderr = (
                    stderr_bytes.decode("utf-8", errors="replace")
                    if stderr_bytes
                    else ""
                )
                return {
                    "stdout": self._truncate_command_output(stdout, max_output_chars),
                    "stderr": self._truncate_command_output(
                        (stderr + "\n命令执行超时。").strip(),
                        max_output_chars,
                    ),
                    "exit_code": -1,
                    "success": False,
                    "timed_out": True,
                }
            stdout = (
                stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            )
            stderr = (
                stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            )
            exit_code = proc.returncode or 0
            return {
                "stdout": self._truncate_command_output(stdout, max_output_chars),
                "stderr": self._truncate_command_output(stderr, max_output_chars),
                "exit_code": exit_code,
                "success": exit_code == 0,
                "timed_out": False,
            }
        except Exception as e:
            logger.error(f"执行 npm 命令失败: {e}")
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "success": False,
                "timed_out": False,
            }

    async def install_dashboard_deps(self):
        """在控制台页面目录执行 `npm ci`。"""
        try:
            guard = getattr(self, "_maintenance_write_guard", lambda: None)()
            if guard:
                return guard
            allow_runtime_build, timeout_seconds, max_output_chars = (
                self._dashboard_runtime_config()
            )
            if not allow_runtime_build:
                return self._dashboard_runtime_build_disabled_response()
            dashboard_dir = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__), "..", "..", "pages", "dashboard"
                )
            )
            pkg_json = os.path.join(dashboard_dir, "package.json")
            if not os.path.isfile(pkg_json):
                return error_response(f"未在以下目录找到 package.json：{dashboard_dir}")

            async with self._get_dashboard_runtime_lock():
                logger.info(f"[控制台页面] 开始在 {dashboard_dir} 安装依赖...")
                result = await self._run_npm_command(
                    ["npm", "ci"],
                    cwd=dashboard_dir,
                    timeout_seconds=timeout_seconds,
                    max_output_chars=max_output_chars,
                )
            logger.info(f"[控制台页面] npm ci 执行完成（退出码={result['exit_code']}）")
            return ok_response({"command": "npm ci", **result})
        except Exception as e:
            logger.error(f"安装控制台页面依赖失败: {e}")
            return error_response(f"安装依赖失败：{e}")

    async def build_dashboard(self):
        """在控制台页面目录执行 `npm run build`。"""
        try:
            guard = getattr(self, "_maintenance_write_guard", lambda: None)()
            if guard:
                return guard
            allow_runtime_build, timeout_seconds, max_output_chars = (
                self._dashboard_runtime_config()
            )
            if not allow_runtime_build:
                return self._dashboard_runtime_build_disabled_response()
            dashboard_dir = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__), "..", "..", "pages", "dashboard"
                )
            )
            pkg_json = os.path.join(dashboard_dir, "package.json")
            if not os.path.isfile(pkg_json):
                return error_response(f"未在以下目录找到 package.json：{dashboard_dir}")

            async with self._get_dashboard_runtime_lock():
                logger.info(f"[控制台页面] 开始在 {dashboard_dir} 构建页面...")
                result = await self._run_npm_command(
                    ["npm", "run", "build"],
                    cwd=dashboard_dir,
                    timeout_seconds=timeout_seconds,
                    max_output_chars=max_output_chars,
                )
            logger.info(
                f"[控制台页面] npm run build 执行完成（退出码={result['exit_code']}）"
            )
            return ok_response({"command": "npm run build", **result})
        except Exception as e:
            logger.error(f"构建控制台页面失败: {e}")
            return error_response(f"构建页面失败：{e}")


__all__ = ["MaintenanceApiMixin"]
