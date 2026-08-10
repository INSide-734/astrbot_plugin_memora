"""备份恢复计划的暂存、应用、验证与回滚状态机。"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from ..domain import (
    BackupIntegrity,
    BackupOperationError,
    BackupType,
    FileRole,
    RestoreFileProgress,
    RestorePlan,
    RestoreStatus,
)
from ..infrastructure import integrity as backup_integrity
from ..infrastructure.snapshot import atomic_write_json, ensure_free_space, sha256_file

_BACKUP_INFO_FILE = "backup_info.json"

_BACKUP_FILE_SPECS: dict[str, tuple[FileRole, str, bool]] = {
    "memora.db": (FileRole.CANONICAL, "sqlite", True),
    "conversations.db": (FileRole.OPERATIONAL, "sqlite", False),
    **backup_integrity.OPERATIONAL_FILE_SPECS,
    "decay_state.json": (FileRole.OPERATIONAL, "regular", False),
    "memora.index": (FileRole.DERIVED, "regular", False),
    "memora_graph.index": (FileRole.DERIVED, "regular", False),
    "memora_graph_documents.db": (FileRole.DERIVED, "sqlite", False),
}

# 全量备份时允许处理的文件或模式，路径均相对于插件数据目录。
_BACKUP_PATTERNS: list[str] = [
    "memora.db",
    "memora.index",
    "memora_graph_documents.db",
    "memora_graph.index",
    "conversations.db",
    *backup_integrity.OPERATIONAL_BACKUP_PATTERNS,
    "decay_state.json",
    "*.db-wal",
    "*.db-shm",
]


class BackupRestoreTransactionMixin:
    """为备份应用服务提供可恢复的文件替换事务。"""

    def _restore_root(self) -> Path:
        """返回恢复事务根目录，并确保目录已经创建。"""

        root = self.data_dir / ".restore"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _plan_to_dict(plan: RestorePlan) -> dict[str, object]:
        """把恢复计划转换为可原子持久化的 JSON 对象。"""

        return {
            "operation_id": plan.operation_id,
            "source_backup_name": plan.source_backup_name,
            "apply_mode": plan.apply_mode,
            "status": plan.status.value,
            "files": [
                {
                    "name": item.name,
                    "role": item.role.value,
                    "moved_to_previous": item.moved_to_previous,
                    "installed": item.installed,
                    "validated": item.validated,
                }
                for item in plan.files
            ],
            "pre_restore_backup_name": plan.pre_restore_backup_name,
            "reason_code": plan.reason_code,
            "reload_scheduled": plan.reload_scheduled,
            "requires_manual_restart": plan.requires_manual_restart,
        }

    @staticmethod
    def _dict_to_plan(value: dict[str, object]) -> RestorePlan:
        """解析持久化恢复计划，并拒绝缺字段或非法枚举值。"""

        files: list[RestoreFileProgress] = []
        for item in value.get("files", []) or []:
            if not isinstance(item, dict):
                raise BackupOperationError("restore_plan_invalid")
            try:
                files.append(
                    RestoreFileProgress(
                        name=str(item["name"]),
                        role=FileRole(str(item["role"])),
                        moved_to_previous=bool(item.get("moved_to_previous", False)),
                        installed=bool(item.get("installed", False)),
                        validated=bool(item.get("validated", False)),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise BackupOperationError("restore_plan_invalid") from exc
        try:
            return RestorePlan(
                operation_id=str(value["operation_id"]),
                source_backup_name=str(value["source_backup_name"]),
                apply_mode=str(value["apply_mode"]),
                status=RestoreStatus(str(value["status"])),
                files=files,
                pre_restore_backup_name=(
                    str(value["pre_restore_backup_name"])
                    if value.get("pre_restore_backup_name")
                    else None
                ),
                reason_code=(
                    str(value["reason_code"]) if value.get("reason_code") else None
                ),
                reload_scheduled=bool(value.get("reload_scheduled", False)),
                requires_manual_restart=bool(
                    value.get("requires_manual_restart", False)
                ),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BackupOperationError("restore_plan_invalid") from exc

    def _plan_path(self, operation_id: str) -> Path:
        """返回已校验操作 ID 对应的恢复计划路径。"""

        self.validate_operation_id(operation_id)
        return self._restore_root() / operation_id / "restore_plan.json"

    def _write_plan(self, plan: RestorePlan) -> None:
        """原子写入恢复计划。"""

        atomic_write_json(self._plan_path(plan.operation_id), self._plan_to_dict(plan))

    def _read_plan(self, operation_id: str | None = None) -> RestorePlan | None:
        """读取指定计划，未指定时优先返回阻塞中的计划。"""

        root = self._restore_root()
        paths = (
            [self._plan_path(operation_id)]
            if operation_id
            else sorted(root.glob("*/restore_plan.json"))
        )
        plans: list[RestorePlan] = []
        for path in paths:
            if not path.exists() or not path.is_file() or path.is_symlink():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise BackupOperationError("restore_plan_invalid")
                plans.append(self._dict_to_plan(value))
            except (OSError, json.JSONDecodeError) as exc:
                raise BackupOperationError("restore_plan_invalid") from exc
        if not plans:
            return None
        active = [plan for plan in plans if self._restore_is_blocking(plan.status)]
        return active[0] if active else plans[-1]

    @staticmethod
    def validate_operation_id(operation_id: str) -> str:
        """校验恢复操作 ID，拒绝空值、路径字符和异常长度。"""

        normalized = str(operation_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", normalized):
            raise ValueError("invalid operation id")
        return normalized

    @staticmethod
    def _restore_is_blocking(status: RestoreStatus) -> bool:
        """判断恢复状态是否应阻止运行时写入。"""

        return status in {
            RestoreStatus.STAGED,
            RestoreStatus.RELOAD_SCHEDULED,
            RestoreStatus.APPLYING,
            RestoreStatus.VALIDATING,
            RestoreStatus.ROLLBACK_PENDING,
            RestoreStatus.ROLLING_BACK,
        }

    def get_maintenance_state(self) -> dict[str, object]:
        """返回不含文件路径和数据内容的维护阻塞状态。"""

        plan = self._read_plan()
        return {
            "blocked": bool(plan and self._restore_is_blocking(plan.status)),
            "operation_id": plan.operation_id
            if plan and self._restore_is_blocking(plan.status)
            else None,
            "status": plan.status.value if plan else None,
        }

    def has_pending_restores(self) -> bool:
        """返回是否存在会阻塞写入的恢复事务。"""

        return bool(self.get_maintenance_state()["blocked"])

    def list_pending_restores(self) -> list[str]:
        """列出当前阻塞中的恢复操作 ID。"""

        plan = self._read_plan()
        if plan and self._restore_is_blocking(plan.status):
            return [plan.operation_id]
        return []

    def _public_restore_status(self, plan: RestorePlan) -> dict[str, object]:
        """把内部恢复计划收敛为允许公开的状态摘要。"""

        return {
            "operation_id": plan.operation_id,
            "source_backup_name": plan.source_backup_name,
            "restore_status": plan.status.value,
            "apply_mode": plan.apply_mode,
            "reload_scheduled": plan.reload_scheduled,
            "requires_manual_restart": plan.requires_manual_restart,
            "pre_restore_backup_name": plan.pre_restore_backup_name,
            "reason_code": plan.reason_code,
        }

    def get_restore_status(
        self, operation_id: str | None = None
    ) -> dict[str, object] | None:
        """返回指定恢复事务或当前事务的公开状态。"""

        plan = self._read_plan(operation_id)
        return self._public_restore_status(plan) if plan else None

    def _migrate_legacy_restore_files(self) -> RestorePlan | None:
        """把旧 ``*.restore`` 文件收敛为单一 staged 恢复计划。"""

        legacy = [path for path in self.data_dir.glob("*.restore") if path.is_file()]
        if not legacy:
            return None
        operation_id = uuid.uuid4().hex
        operation_dir = self._restore_root() / operation_id
        payload_dir = operation_dir / "payload"
        payload_dir.mkdir(parents=True, exist_ok=False)
        files: list[RestoreFileProgress] = []
        for source in legacy:
            name = source.name.removesuffix(".restore")
            if not self._is_restorable_file_name(name):
                continue
            os.replace(source, payload_dir / name)
            role = _BACKUP_FILE_SPECS.get(
                name, (FileRole.OPERATIONAL, "regular", False)
            )[0]
            files.append(RestoreFileProgress(name=name, role=role))
        if not files:
            shutil.rmtree(operation_dir, ignore_errors=True)
            return None
        plan = RestorePlan(
            operation_id=operation_id,
            source_backup_name="legacy",
            apply_mode="restart",
            status=RestoreStatus.STAGED,
            files=files,
            requires_manual_restart=True,
            reason_code="legacy_restore_migrated",
        )
        self._write_plan(plan)
        return plan

    def _quick_check(self, path: Path) -> str:
        """运行 SQLite quick check，并把损坏结果映射为稳定错误码。"""

        connection = sqlite3.connect(str(path))
        try:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            connection.close()
        if result.lower() != "ok":
            raise BackupOperationError("backup_invalid")
        return result

    def _preflight_restore(self, backup_name: str) -> dict[str, object]:
        """验证备份清单、文件摘要、角色和 SQLite 完整性。"""

        backup_name = self.validate_backup_name(backup_name)
        backup_dir = self.get_backup_dir(backup_name)
        if backup_dir is None:
            raise FileNotFoundError("backup_not_found")
        info_path = backup_dir / _BACKUP_INFO_FILE
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupOperationError("backup_invalid") from exc
        if not isinstance(info, dict):
            raise BackupOperationError("backup_invalid")
        manifest_files = info.get("files")
        verified = info.get("manifest_version") == 2 and isinstance(
            manifest_files, dict
        )
        file_specs: list[dict[str, object]] = []
        if verified:
            if info.get("status") != "ready":
                raise BackupOperationError("backup_invalid")
            for name, metadata in manifest_files.items():
                if name not in _BACKUP_FILE_SPECS or not isinstance(metadata, dict):
                    raise BackupOperationError("backup_invalid")
                source = backup_dir / name
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or Path(name).name != name
                    or name in {"*.db-wal", "*.db-shm"}
                ):
                    raise BackupOperationError("backup_invalid")
                if source.stat().st_size != int(metadata.get("size_bytes", -1)):
                    raise BackupOperationError("backup_invalid")
                if sha256_file(source) != str(metadata.get("sha256", "")):
                    raise BackupOperationError("backup_invalid")
                role = FileRole(str(metadata.get("role", "")))
                expected_role = _BACKUP_FILE_SPECS[name][0]
                if role is not expected_role:
                    raise BackupOperationError("backup_invalid")
                if str(metadata.get("kind", "regular")) == "sqlite":
                    self._quick_check(source)
                if role is not FileRole.DERIVED:
                    file_specs.append({"name": name, "role": role.value})
            if not any(item["name"] == "memora.db" for item in file_specs):
                raise BackupOperationError("canonical_file_missing")
            integrity = BackupIntegrity.VERIFIED.value
        else:
            integrity = BackupIntegrity.LEGACY_UNVERIFIED.value
            for source in backup_dir.iterdir():
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or source.name == _BACKUP_INFO_FILE
                    or not self._is_restorable_file_name(source.name)
                ):
                    continue
                role = _BACKUP_FILE_SPECS.get(
                    source.name, (FileRole.OPERATIONAL, "regular", False)
                )[0]
                if role is not FileRole.DERIVED:
                    file_specs.append({"name": source.name, "role": role.value})
            if not any(item["name"] == "memora.db" for item in file_specs):
                raise BackupOperationError("canonical_file_missing")
        backup_integrity.validate_feedback_backup_specs(backup_dir, file_specs)
        return {
            "backup_name": backup_name,
            "backup_dir": backup_dir,
            "file_specs": file_specs,
            "integrity": integrity,
            "warning_codes": ["legacy_unverified"] if not verified else [],
        }

    def stage_restore(
        self, name: str, apply_mode: str = "restart"
    ) -> dict[str, object]:
        """校验备份并把恢复文件暂存为一个有状态事务。"""

        if apply_mode not in {"reload", "restart"}:
            raise ValueError("invalid apply mode")
        current = self._read_plan()
        if current and self._restore_is_blocking(current.status):
            raise BackupOperationError("restore_conflict")
        preflight = self._preflight_restore(name)
        file_specs = preflight["file_specs"]
        assert isinstance(file_specs, list)
        operation_id = uuid.uuid4().hex
        operation_dir = self._restore_root() / operation_id
        payload_dir = operation_dir / "payload"
        payload_dir.mkdir(parents=True, exist_ok=False)
        try:
            required = sum(
                (preflight["backup_dir"] / str(item["name"])).stat().st_size
                for item in file_specs
            )
            ensure_free_space(self.data_dir, required)
            progress: list[RestoreFileProgress] = []
            for item in file_specs:
                filename = str(item["name"])
                shutil.copy2(preflight["backup_dir"] / filename, payload_dir / filename)
                progress.append(
                    RestoreFileProgress(name=filename, role=FileRole(str(item["role"])))
                )
            plan = RestorePlan(
                operation_id=operation_id,
                source_backup_name=str(preflight["backup_name"]),
                apply_mode=apply_mode,
                status=RestoreStatus.STAGED,
                files=progress,
                requires_manual_restart=apply_mode == "restart",
                reason_code=(
                    "legacy_unverified"
                    if preflight["integrity"] == "legacy_unverified"
                    else None
                ),
            )
            self._write_plan(plan)
            return {
                **self._public_restore_status(plan),
                "name": plan.source_backup_name,
                "staged": len(progress),
                "pending": True,
                "warning_codes": preflight["warning_codes"],
            }
        except Exception:
            shutil.rmtree(operation_dir, ignore_errors=True)
            raise

    def create_pre_restore_backup(self, operation_id: str) -> dict[str, object] | None:
        """在覆盖 live canonical 前创建并记录保护性备份。"""

        plan = self._read_plan(operation_id)
        if plan is None:
            raise BackupOperationError("restore_not_found")
        if not (self.data_dir / "memora.db").is_file():
            return None
        result = self._create_backup_sync(BackupType.PRE_RESTORE, None)
        plan.pre_restore_backup_name = str(result["name"])
        self._write_plan(plan)
        return result

    def _install_restore_file(
        self, plan_dir: Path, progress: RestoreFileProgress
    ) -> RestoreFileProgress:
        """原子安装一个 payload 文件，并保留可回滚的 live 文件。"""

        payload = plan_dir / "payload" / progress.name
        target = self.data_dir / progress.name
        previous = plan_dir / "previous" / progress.name
        previous.parent.mkdir(parents=True, exist_ok=True)
        if not payload.is_file() or payload.is_symlink():
            raise BackupOperationError("restore_apply_failed")
        if target.is_symlink():
            raise BackupOperationError("restore_apply_failed")
        if target.exists():
            os.replace(target, previous)
            progress = RestoreFileProgress(
                name=progress.name,
                role=progress.role,
                moved_to_previous=True,
                installed=progress.installed,
                validated=progress.validated,
            )
        os.replace(payload, target)
        return RestoreFileProgress(
            name=progress.name,
            role=progress.role,
            moved_to_previous=progress.moved_to_previous,
            installed=True,
            validated=progress.validated,
        )

    def apply_pending_restores(self) -> dict[str, object]:
        """在数据库打开前应用恢复计划，并在失败时回滚。"""

        plan = self._read_plan()
        if plan is None:
            plan = self._migrate_legacy_restore_files()
        if plan is None:
            return {"status": "none", "applied": 0}
        if plan.status is RestoreStatus.ROLLBACK_PENDING:
            backup_integrity.rollback_restore_files(
                self.data_dir, self._restore_root(), plan, self._write_plan
            )
            return self._public_restore_status(plan)
        if plan.status not in {
            RestoreStatus.STAGED,
            RestoreStatus.RELOAD_SCHEDULED,
            RestoreStatus.APPLYING,
            RestoreStatus.VALIDATING,
        }:
            return self._public_restore_status(plan)
        started_apply = plan.status in {
            RestoreStatus.APPLYING,
            RestoreStatus.VALIDATING,
        }
        try:
            if plan.status in {RestoreStatus.STAGED, RestoreStatus.RELOAD_SCHEDULED}:
                self.create_pre_restore_backup(plan.operation_id)
                plan = self._read_plan(plan.operation_id) or plan
                plan.status = RestoreStatus.APPLYING
                self._write_plan(plan)
                started_apply = True
            plan_dir = self._restore_root() / plan.operation_id
            for index, progress in enumerate(plan.files):
                if progress.installed:
                    continue
                plan.files[index] = self._install_restore_file(plan_dir, progress)
                self._write_plan(plan)
            backup_integrity.validate_restored_files(
                self.data_dir, plan, self._quick_check
            )
            plan.status = RestoreStatus.VALIDATING
            for index, progress in enumerate(plan.files):
                plan.files[index] = RestoreFileProgress(
                    name=progress.name,
                    role=progress.role,
                    moved_to_previous=progress.moved_to_previous,
                    installed=progress.installed,
                    validated=True,
                )
            self._write_plan(plan)
            return self._public_restore_status(plan)
        except (OSError, sqlite3.DatabaseError, BackupOperationError) as exc:
            plan.reason_code = (
                str(exc)
                if isinstance(exc, BackupOperationError)
                else "restore_apply_failed"
            )
            if not started_apply:
                plan.status = RestoreStatus.FAILED_BEFORE_APPLY
                self._write_plan(plan)
                return self._public_restore_status(plan)
            try:
                backup_integrity.rollback_restore_files(
                    self.data_dir, self._restore_root(), plan, self._write_plan
                )
            except BackupOperationError:
                return self._public_restore_status(plan)
            return self._public_restore_status(plan)

    def mark_restore_succeeded(self, operation_id: str | None = None) -> None:
        """在运行时初始化成功后完成恢复并清理事务文件。"""

        plan = self._read_plan(operation_id)
        if plan is None:
            return
        plan.status = RestoreStatus.SUCCEEDED
        plan.reason_code = None
        self._write_plan(plan)
        shutil.rmtree(
            self._restore_root() / plan.operation_id / "payload", ignore_errors=True
        )
        shutil.rmtree(
            self._restore_root() / plan.operation_id / "previous", ignore_errors=True
        )

    def mark_restore_startup_failure_if_needed(self, failed: bool) -> None:
        """把恢复后的启动失败标为待回滚，供下次启动处理。"""

        if not failed:
            return
        plan = self._read_plan()
        if plan and plan.status is RestoreStatus.VALIDATING:
            plan.status = RestoreStatus.ROLLBACK_PENDING
            plan.reason_code = "restore_startup_failed"
            self._write_plan(plan)

    def mark_reload_scheduled(self, operation_id: str, scheduled: bool) -> None:
        """记录热重载调度结果，并在失败时要求手动重启。"""

        plan = self._read_plan(operation_id)
        if plan is None:
            raise BackupOperationError("restore_not_found")
        plan.reload_scheduled = scheduled
        if scheduled:
            plan.status = RestoreStatus.RELOAD_SCHEDULED
            plan.requires_manual_restart = False
        else:
            plan.status = RestoreStatus.STAGED
            plan.requires_manual_restart = True
            plan.reason_code = "hot_reload_schedule_failed"
        self._write_plan(plan)

    def cancel_restore(self, operation_id: str) -> dict[str, object]:
        """取消尚未应用的 staged 恢复并清理暂存文件。"""

        plan = self._read_plan(operation_id)
        if plan is None:
            raise BackupOperationError("restore_not_found")
        if plan.status is not RestoreStatus.STAGED:
            raise BackupOperationError("restore_cancel_not_allowed")
        operation_dir = self._restore_root() / plan.operation_id
        plan.status = RestoreStatus.CANCELLED
        plan.reason_code = None
        self._write_plan(plan)
        shutil.rmtree(operation_dir / "payload", ignore_errors=True)
        shutil.rmtree(operation_dir / "previous", ignore_errors=True)
        return self._public_restore_status(plan)

    @staticmethod
    def _is_restorable_file_name(name: str) -> bool:
        """判断文件名是否位于恢复白名单且不包含路径字符。"""

        if Path(name).name != name or Path(name).is_absolute():
            return False
        if any(sep and sep in name for sep in ("/", "\\", os.sep, os.altsep)):
            return False
        return any(fnmatch.fnmatch(name, pattern) for pattern in _BACKUP_PATTERNS)


__all__ = ["BackupRestoreTransactionMixin"]
