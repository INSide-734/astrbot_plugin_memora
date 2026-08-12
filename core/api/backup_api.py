"""备份、恢复和恢复状态 Page API。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, cast

from astrbot.api import logger

from ..features.backup.application import BackupManager
from ..features.backup.domain import BackupOperationError
from .response_utils import error_response, ok_response

_BACKUP_ERROR_CODES = {
    "invalid_backup_type",
    "backup_not_found",
    "backup_busy",
    "backup_create_failed",
    "backup_invalid",
    "canonical_file_missing",
    "backup_name_conflict",
    "restore_conflict",
    "restore_not_found",
    "restore_cancel_not_allowed",
    "restore_apply_failed",
    "restore_rollback_pending",
    "restore_plan_invalid",
}


def _safe_backup_list(backups: object) -> list[dict[str, object]]:
    """把未知备份列表收窄为可公开处理的字典列表。"""

    if isinstance(backups, list):
        return [item for item in backups if isinstance(item, dict)]
    if backups is None or isinstance(backups, (str, bytes, bytearray, Mapping)):
        return []
    try:
        return [
            item for item in cast(Iterable[object], backups) if isinstance(item, dict)
        ]
    except TypeError:
        return []


def _safe_operation_error(exc: Exception, fallback: str) -> dict[str, Any]:
    if isinstance(exc, BackupOperationError):
        code = str(exc) if str(exc) in _BACKUP_ERROR_CODES else fallback
        return error_response("备份操作失败，请稍后重试。", code=code)
    if isinstance(exc, FileNotFoundError):
        return error_response("未找到备份。", code="backup_not_found")
    if isinstance(exc, ValueError):
        return error_response("请求参数无效。", code="invalid_request")
    logger.error(
        "[备份接口] operation=%s error_class=%s",
        fallback,
        type(exc).__name__,
    )
    return error_response("备份操作失败，请稍后重试。", code=fallback)


class BackupApiMixin:
    """备份与恢复接口混入。"""

    plugin: Any

    if TYPE_CHECKING:

        async def _ensure_plugin_ready(
            self,
        ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            """声明由组合页面 API 提供的就绪检查。"""

            ...

    def _backup_manager(self):
        manager = getattr(self.plugin, "_backup_manager", None)
        return manager if manager is not None else None

    @staticmethod
    def _json_object_payload_or_error(
        payload: object,
    ) -> tuple[dict[Any, Any] | None, dict[str, Any] | None]:
        """校验 JSON 对象，并返回可直接使用的载荷和可选错误。"""

        if isinstance(payload, dict):
            return payload, None
        return None, error_response("请求体必须为 JSON 对象", code="invalid_request")

    @staticmethod
    def _public_backup_item(item: object) -> dict[str, object]:
        # 兼容旧管理器返回的单一路径字符串；只保留文件名，避免泄漏主机路径。
        if isinstance(item, str):
            from pathlib import PurePath

            name = PurePath(item).name
            return {"path": name} if name and name not in {".", ".."} else {}
        if not isinstance(item, Mapping):
            return {}
        allowed = {
            "name",
            "backup_type",
            "created_at",
            "backup_timestamp",
            "plugin_version",
            "manifest_version",
            "status",
            "integrity",
            "files",
            "file_count",
            "total_size_bytes",
            "warning_codes",
            "can_restore",
            "can_hot_restore",
            # 旧接口的相对文件名兼容字段；绝对路径和目录字段永不透传。
            "path",
        }
        public: dict[str, object] = {}
        for key in allowed:
            if key not in item:
                continue
            value = item[key]
            if key == "path":
                from pathlib import PurePath

                path = str(value)
                path_name = PurePath(path).name
                if path != path_name or path_name in {"", ".", ".."}:
                    continue
                value = path_name
            public[key] = value
        return public

    async def create_backup(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = BackupApiMixin._backup_manager(self)
        if manager is None:
            return error_response("备份管理器不可用", code="backup_create_failed")
        try:
            result = await manager.create_backup(kind="manual")
            backup = BackupApiMixin._public_backup_item(result)
            return ok_response(
                {
                    "message": "备份创建完成",
                    "backup": backup,
                    # 旧客户端读取顶层 path；新客户端使用 backup 摘要。
                    **({"path": backup["path"]} if "path" in backup else {}),
                }
            )
        except Exception as exc:
            return _safe_operation_error(exc, "backup_create_failed")

    async def list_backups(self):
        manager = BackupApiMixin._backup_manager(self)
        try:
            if manager is not None:
                data_dir = str(manager.data_dir)
            else:
                initializer = getattr(self.plugin, "initializer", None)
                data_dir = str(getattr(initializer, "data_dir", "") or "")
            if not data_dir:
                return ok_response(
                    {
                        "backups": [],
                        "total": 0,
                        "pending_restore": None,
                        "capabilities": {"hot_reload": False},
                    }
                )
            backups = [
                BackupApiMixin._public_backup_item(item)
                for item in _safe_backup_list(BackupManager.list_backups(data_dir))
            ]
            pending = manager.get_restore_status() if manager is not None else None
            supports_reload = getattr(self.plugin, "supports_plugin_reload", None)
            reload_value = supports_reload() if callable(supports_reload) else False
            hot_reload = reload_value if isinstance(reload_value, bool) else False
            for item in backups:
                # 新格式摘要带有 can_restore；旧客户端伪造的极简摘要不额外注入字段。
                if "can_restore" in item or hot_reload:
                    item["can_hot_restore"] = (
                        bool(item.get("can_restore", False)) and hot_reload
                    )
            return ok_response(
                {
                    "backups": backups,
                    "total": len(backups),
                    "pending_restore": pending,
                    "capabilities": {"hot_reload": hot_reload},
                }
            )
        except Exception as exc:
            return _safe_operation_error(exc, "backup_list_failed")

    async def restore_backup(self):
        """校验并暂存指定备份的恢复事务。"""

        from quart import request

        payload, error = BackupApiMixin._json_object_payload_or_error(
            await request.get_json(silent=True)
        )
        if error:
            return error
        assert payload is not None
        unknown = set(payload) - {"name", "apply_mode"}
        if unknown:
            return error_response("请求字段无效", code="invalid_request")
        name = str(payload.get("name", "")).strip()
        apply_mode = str(payload.get("apply_mode", "restart")).strip().lower()
        if not name:
            return error_response("缺少备份名称", code="invalid_request")
        if apply_mode not in {"reload", "restart"}:
            return error_response(
                "apply_mode 必须是 reload 或 restart", code="invalid_request"
            )
        try:
            name = BackupManager.validate_backup_name(name)
        except ValueError:
            return error_response("备份名称无效", code="invalid_backup_name")
        manager = BackupApiMixin._backup_manager(self)
        if manager is None:
            return error_response("备份管理器不可用", code="backup_invalid")
        try:
            result = manager.stage_restore(name, apply_mode=apply_mode)
            if not isinstance(result, Mapping):
                return error_response("恢复计划无效", code="restore_plan_invalid")
            operation_id = str(result.get("operation_id", "")).strip()
            if not operation_id:
                # 兼容旧管理器的摘要返回；新事务必须包含 operation_id。
                def _legacy_count(value: Any) -> int:
                    """把旧管理器计数字段规范化为非负整数。"""

                    if isinstance(value, bool):
                        return 0
                    try:
                        return max(0, int(value))
                    except (TypeError, ValueError):
                        return 0

                def _legacy_files(value: object) -> list[str]:
                    """把旧管理器文件字段规范化为字符串列表。"""

                    if isinstance(value, (list, tuple)):
                        return [str(item) for item in value if isinstance(item, str)]
                    return []

                return ok_response(
                    {
                        "staged": _legacy_count(result.get("staged", 0)),
                        "skipped": _legacy_count(result.get("skipped", 0)),
                        "pending": bool(result.get("pending", True)),
                        "staged_files": _legacy_files(result.get("staged_files")),
                        "skipped_files": _legacy_files(result.get("skipped_files")),
                        "warning_codes": _legacy_files(result.get("warning_codes")),
                        "message": "备份已校验并暂存，请重启 AstrBot 完成恢复。",
                    }
                )
            scheduled = False
            if apply_mode == "reload":
                schedule = getattr(self.plugin, "schedule_backup_restore_reload", None)
                scheduled = (
                    bool(schedule(operation_id)) if callable(schedule) else False
                )
                manager.mark_reload_scheduled(operation_id, scheduled)
            status = manager.get_restore_status(operation_id) or result
            message = (
                "备份已校验并暂存，插件热重载已安排。"
                if scheduled
                else "备份已校验并暂存，请重启 AstrBot 完成恢复。"
            )
            return ok_response(
                {
                    **status,
                    "message": message,
                    "staged": result.get("staged", 0),
                    "pending": True,
                    "warning_codes": result.get("warning_codes", []),
                }
            )
        except Exception as exc:
            return _safe_operation_error(exc, "restore_apply_failed")

    async def get_backup_status(self):
        from quart import request

        manager = BackupApiMixin._backup_manager(self)
        if manager is None:
            return ok_response({"restore_status": None})
        operation_id = (
            str((getattr(request, "args", {}) or {}).get("operation_id", "")) or None
        )
        try:
            status = manager.get_restore_status(operation_id)
            if operation_id and status is None:
                return error_response("未找到恢复事务", code="restore_not_found")
            return ok_response(status)
        except Exception as exc:
            return _safe_operation_error(exc, "restore_plan_invalid")

    async def cancel_restore(self):
        """取消尚未应用的备份恢复事务。"""

        from quart import request

        payload, error = BackupApiMixin._json_object_payload_or_error(
            await request.get_json(silent=True)
        )
        if error:
            return error
        assert payload is not None
        operation_id = str(payload.get("operation_id", "")).strip()
        if not operation_id:
            return error_response("缺少恢复事务 ID", code="invalid_request")
        manager = BackupApiMixin._backup_manager(self)
        if manager is None:
            return error_response("备份管理器不可用", code="restore_not_found")
        try:
            return ok_response(
                {
                    "message": "已取消暂存恢复",
                    **manager.cancel_restore(operation_id),
                }
            )
        except Exception as exc:
            return _safe_operation_error(exc, "restore_cancel_not_allowed")

    async def delete_backup(self):
        """删除一个通过名称校验且未被占用的备份。"""

        from quart import request

        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload, error = BackupApiMixin._json_object_payload_or_error(
            await request.get_json(silent=True)
        )
        if error:
            return error
        assert payload is not None
        name = str(payload.get("name", "")).strip()
        if not name:
            return error_response("缺少备份名称", code="invalid_request")
        try:
            name = BackupManager.validate_backup_name(name)
        except ValueError:
            return error_response("备份名称无效", code="invalid_backup_name")
        manager = BackupApiMixin._backup_manager(self)
        if manager is None:
            return error_response("备份管理器不可用", code="backup_not_found")
        try:
            if not manager.delete_backup(name):
                return error_response("未找到备份", code="backup_not_found")
            return ok_response({"message": "备份已删除", "name": name})
        except Exception as exc:
            return _safe_operation_error(exc, "backup_in_use")

    async def batch_delete_backups(self):
        """批量删除一至一百个经过名称校验的备份。"""

        from quart import request

        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload, error = BackupApiMixin._json_object_payload_or_error(
            await request.get_json(silent=True)
        )
        if error:
            return error
        assert payload is not None
        names = payload.get("names", [])
        if not isinstance(names, list) or not names or len(names) > 100:
            return error_response(
                "names 必须是 1 到 100 个名称", code="invalid_request"
            )
        manager = BackupApiMixin._backup_manager(self)
        if manager is None:
            return error_response("备份管理器不可用", code="backup_not_found")
        deleted_names: list[str] = []
        failed_items: list[dict[str, str]] = []
        for raw_name in names:
            name = str(raw_name).strip()
            try:
                normalized = BackupManager.validate_backup_name(name)
                if manager.delete_backup(normalized):
                    deleted_names.append(normalized)
                else:
                    failed_items.append(
                        {"name": normalized, "reason_code": "backup_not_found"}
                    )
            except ValueError:
                failed_items.append(
                    {"name": name, "reason_code": "invalid_backup_name"}
                )
            except Exception as exc:
                code = str(exc) if str(exc) in _BACKUP_ERROR_CODES else "backup_in_use"
                failed_items.append({"name": name, "reason_code": code})
        return ok_response(
            {
                "message": f"已删除 {len(deleted_names)}/{len(names)} 个备份",
                "requested": len(names),
                "deleted": len(deleted_names),
                "failed": len(failed_items),
                "deleted_names": deleted_names,
                "failed_items": failed_items,
            }
        )
