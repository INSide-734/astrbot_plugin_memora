"""控制台页面的维护操作接口（重建、清理、压缩、备份）。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from collections.abc import Mapping

from astrbot.api import logger

from .response_utils import error_response, ok_response
from ..validators.persistence_health_validator import PersistenceHealthValidator


class MaintenanceApiMixin:
    _dashboard_runtime_lock: asyncio.Lock | None = None

    @staticmethod
    def _json_object_payload_or_error(payload):
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
            result = await validator.rebuild_indexes(engine)
            self._record_index_rebuild_observability(
                result,
                time.perf_counter() - started_at,
            )
            return ok_response({"message": "索引重建完成", "result": result})
        except Exception as e:
            elapsed = time.perf_counter() - started_at if "started_at" in locals() else 0.0
            self._record_index_rebuild_observability({}, elapsed, error=e)
            logger.error(f"索引重建失败: {e}")
            return error_response(f"索引重建失败：{e}")

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
            if not hasattr(engine, "rebuild_graph_index"):
                return error_response("图索引重建能力不可用")
            result = await engine.rebuild_graph_index()
            return ok_response(
                {"message": "图索引重建完成", "result": result}
            )
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
            index_validator = getattr(initializer, "index_validator", None)
            db_path = (
                getattr(index_validator, "db_path", None)
                or getattr(engine, "db_path", None)
            )
            if not db_path:
                return error_response("数据库路径不可用")
            graph_manager = getattr(engine, "graph_memory_manager", None)
            graph_faiss_db = (
                getattr(graph_manager, "faiss_db", None)
                or getattr(graph_manager, "graph_faiss_db", None)
            )
            validator = PersistenceHealthValidator(
                db_path,
                getattr(index_validator, "faiss_db", None)
                or getattr(engine, "faiss_db", None),
                graph_faiss_db,
            )
            return ok_response(await validator.check())
        except Exception as e:
            logger.error(f"持久化健康检查失败: {e}", exc_info=True)
            return error_response(f"持久化健康检查失败：{e}")

    async def repair_persistence_health(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        from quart import request

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._json_object_payload_or_error(payload)
        if error:
            return error
        targets = payload.get("targets")
        if not isinstance(targets, list) or not targets:
            return error_response("repair requires explicit targets")
        return error_response(
            "持久化健康修复需要按目标逐项实现；当前仅提供只读报告，"
            f"收到 targets={targets}"
        )

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

    async def create_backup(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        try:
            plugin = self.plugin
            if (
                hasattr(plugin, "_backup_manager")
                and plugin._backup_manager is not None
            ):
                backup_path = await plugin._backup_manager.create_backup()
                return ok_response({"message": "备份创建完成", "path": backup_path})
            return error_response("备份管理器不可用")
        except Exception as e:
            logger.error(f"创建备份失败: {e}")
            return error_response(f"创建备份失败：{e}")

    async def list_backups(self):
        """返回备份列表（供 SystemPage 展示）。"""
        try:
            plugin = self.plugin
            if (
                hasattr(plugin, "_backup_manager")
                and plugin._backup_manager is not None
            ):
                data_dir = str(plugin._backup_manager.data_dir)
            else:
                data_dir = (
                    self.plugin.initializer.data_dir if self.plugin.initializer else ""
                )
            if not data_dir:
                return ok_response({"backups": [], "total": 0})
            from ..managers.backup_manager import BackupManager

            backups = BackupManager.list_backups(data_dir)
            backups = MaintenanceApiMixin._coerce_result_list(backups)
            return ok_response({"backups": backups, "total": len(backups)})
        except Exception as e:
            logger.error(f"获取备份列表失败: {e}")
            return error_response(f"获取备份列表失败：{e}")

    async def delete_backup(self):
        """删除单个备份。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        from quart import request
        from ..managers.backup_manager import BackupManager

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._json_object_payload_or_error(payload)
        if error:
            return error
        backup_name = str(payload.get("name", "")).strip()
        if not backup_name:
            return error_response("缺少备份名称")
        try:
            backup_name = BackupManager.validate_backup_name(backup_name)
        except ValueError as e:
            return error_response(str(e))

        try:
            plugin = self.plugin
            if not hasattr(plugin, "_backup_manager") or plugin._backup_manager is None:
                return error_response("备份管理器不可用")
            deleted = plugin._backup_manager.delete_backup(backup_name)
            if not deleted:
                return error_response(f"未找到备份：{backup_name}")
            return ok_response(
                {"message": f"已删除备份 {backup_name}", "name": backup_name}
            )
        except ValueError as e:
            return error_response(str(e))
        except Exception as e:
            logger.error(f"删除备份失败: {e}")
            return error_response(f"删除备份失败：{e}")

    async def batch_delete_backups(self):
        """批量删除备份。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        from quart import request
        from ..managers.backup_manager import BackupManager

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._json_object_payload_or_error(payload)
        if error:
            return error
        names: list[str] = payload.get("names", []) or []
        if not isinstance(names, list):
            return error_response("names 必须为列表")
        if not names:
            return error_response("缺少待删除的备份名称列表")

        try:
            plugin = self.plugin
            if not hasattr(plugin, "_backup_manager") or plugin._backup_manager is None:
                return error_response("备份管理器不可用")
            bm = plugin._backup_manager
            deleted, failed = 0, 0
            for name in names:
                try:
                    normalized_name = BackupManager.validate_backup_name(name)
                    if bm.delete_backup(normalized_name):
                        deleted += 1
                    else:
                        failed += 1
                except ValueError as exc:
                    logger.debug(
                        "[维护接口] 批量删除时发现非法备份名: %s",
                        exc,
                        exc_info=True,
                    )
                    failed += 1
                except Exception as exc:
                    logger.error(
                        "[维护接口] 删除备份失败，名称=%s: %s",
                        name,
                        exc,
                        exc_info=True,
                    )
                    failed += 1
            return ok_response(
                {
                    "message": f"已删除 {deleted}/{len(names)} 个备份",
                    "deleted": deleted,
                    "failed": failed,
                }
            )
        except Exception as e:
            logger.error(f"批量删除备份失败: {e}")
            return error_response(f"批量删除备份失败：{e}")

    async def restore_backup(self):
        """从指定备份目录暂存恢复数据，重启后在 DB 打开前生效。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        from quart import request
        from ..managers.backup_manager import BackupManager

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._json_object_payload_or_error(payload)
        if error:
            return error
        backup_name = str(payload.get("name", "")).strip()
        if not backup_name:
            return error_response("缺少备份名称")
        try:
            backup_name = BackupManager.validate_backup_name(backup_name)
        except ValueError as e:
            return error_response(str(e))

        try:
            plugin = self.plugin
            if not hasattr(plugin, "_backup_manager") or plugin._backup_manager is None:
                return error_response("备份管理器不可用")
            bm = plugin._backup_manager
            result = bm.stage_restore(backup_name)
            if not isinstance(result, dict):
                result = {}
            staged = MaintenanceApiMixin._coerce_result_int(result.get("staged", 0), 0)
            skipped = MaintenanceApiMixin._coerce_result_int(result.get("skipped", 0), 0)
            staged_files = MaintenanceApiMixin._coerce_result_str_list(
                result.get("staged_files", [])
            )
            skipped_files = MaintenanceApiMixin._coerce_result_str_list(
                result.get("skipped_files", [])
            )
            if staged <= 0:
                return error_response(f"备份 {backup_name} 中没有可恢复文件")
            msg = (
                f"已暂存 {staged} 个恢复文件，{skipped} 个文件已跳过。"
                f"请重启 AstrBot 完成恢复；重启前写入操作已进入维护保护。"
            )
            logger.info(f"[备份管理] {msg}")
            return ok_response(
                {
                    "message": msg,
                    "restored": 0,
                    "staged": staged,
                    "skipped": skipped,
                    "pending": True,
                    "staged_files": staged_files,
                    "skipped_files": skipped_files,
                }
            )
        except FileNotFoundError as e:
            return error_response(str(e))
        except ValueError as e:
            return error_response(str(e))
        except Exception as e:
            logger.error(f"恢复备份失败: {e}")
            return error_response(f"恢复备份失败：{e}")

    async def export_memories(self):
        """导出记忆为 JSONL 或 Markdown（返回内联内容用于浏览器下载）。"""
        from quart import request

        payload = await request.get_json(silent=True) or {}
        payload, error = MaintenanceApiMixin._json_object_payload_or_error(payload)
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

            if export_format == "markdown":
                count = await exporter.export_markdown(tmp_path)
            else:
                count = await exporter.export_jsonl(tmp_path)
            count = MaintenanceApiMixin._coerce_result_int(count, 0)

            with open(tmp_path, encoding="utf-8") as f:
                content = f.read()

            os.unlink(tmp_path)

            return ok_response(
                {
                    "content": content,
                    "count": count,
                    "format": export_format,
                }
            )
        except Exception as e:
            logger.error(f"导出记忆失败: {e}")
            return error_response(f"导出记忆失败：{e}")


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
                resolved_node, "--version",
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
                stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
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
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
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
                os.path.join(os.path.dirname(__file__), "..", "..", "pages", "dashboard")
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
            logger.info(
                f"[控制台页面] npm ci 执行完成（退出码={result['exit_code']}）"
            )
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
                os.path.join(os.path.dirname(__file__), "..", "..", "pages", "dashboard")
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
