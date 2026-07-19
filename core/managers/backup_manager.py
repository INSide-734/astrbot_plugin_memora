"""版本变更触发的数据备份管理器。"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import logger

from .backup_models import (
    BackupIntegrity,
    BackupOperationError,
    BackupType,
    FileRole,
    RestoreFileProgress,
    RestorePlan,
    RestoreStatus,
)
from .backup_snapshot import (
    atomic_write_json,
    copy_regular_file,
    ensure_free_space,
    sha256_file,
    snapshot_sqlite,
)
from ..utils.version import PLUGIN_VERSION  # single source of truth: metadata.yaml

_VERSION_FILE = ".plugin_version"
_BACKUP_INFO_FILE = "backup_info.json"
_BACKUP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_BACKUP_FILE_SPECS: dict[str, tuple[FileRole, str, bool]] = {
    "memora.db": (FileRole.CANONICAL, "sqlite", True),
    "conversations.db": (FileRole.OPERATIONAL, "sqlite", False),
    "decay_state.json": (FileRole.OPERATIONAL, "regular", False),
    "memora.index": (FileRole.DERIVED, "regular", False),
    "memora_graph.index": (FileRole.DERIVED, "regular", False),
    "memora_graph_documents.db": (FileRole.DERIVED, "sqlite", False),
}

# 全量备份时包含的文件/模式（相对于 data_dir）
_BACKUP_PATTERNS: list[str] = [
    "memora.db",
    "memora.index",
    "memora_graph_documents.db",
    "memora_graph.index",
    "conversations.db",
    "decay_state.json",
    "*.db-wal",
    "*.db-shm",
]


class BackupManager:
    """检测版本变化并创建完整数据备份。"""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.version_file = self.data_dir / _VERSION_FILE
        self._operation_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_stored_version(self) -> str | None:
        """返回上一次记录的插件版本；首次运行时返回 ``None``。"""
        if not self.version_file.exists():
            return None
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def write_current_version(self) -> None:
        """持久化当前插件版本。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.version_file.write_text(PLUGIN_VERSION, encoding="utf-8")

    def needs_backup(self) -> bool:
        """若插件版本发生变化或首次运行，则返回 ``True``。"""
        stored = self.get_stored_version()
        if stored is None:
            return True  # 首次安装时也做一次备份，提升安全性
        return stored != PLUGIN_VERSION

    def backup_if_needed(self) -> dict[str, object] | None:
        """在版本变更时创建完整备份。"""
        if not self.needs_backup():
            return None

        stored = self.get_stored_version()
        old_label = stored or "unknown"
        if not self._has_backup_data():
            self.write_current_version()
            logger.info("[备份管理] 首次安装没有可备份数据，仅更新版本标记")
            return None

        result = self._create_backup_sync(BackupType.VERSION_CHANGE, old_label)
        self.write_current_version()
        return result

    def _restore_root(self) -> Path:
        root = self.data_dir / ".restore"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _plan_to_dict(plan: RestorePlan) -> dict[str, object]:
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
                reason_code=(str(value["reason_code"]) if value.get("reason_code") else None),
                reload_scheduled=bool(value.get("reload_scheduled", False)),
                requires_manual_restart=bool(value.get("requires_manual_restart", False)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BackupOperationError("restore_plan_invalid") from exc

    def _plan_path(self, operation_id: str) -> Path:
        self.validate_operation_id(operation_id)
        return self._restore_root() / operation_id / "restore_plan.json"

    def _write_plan(self, plan: RestorePlan) -> None:
        atomic_write_json(self._plan_path(plan.operation_id), self._plan_to_dict(plan))

    def _read_plan(self, operation_id: str | None = None) -> RestorePlan | None:
        root = self._restore_root()
        paths = [self._plan_path(operation_id)] if operation_id else sorted(root.glob("*/restore_plan.json"))
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
        normalized = str(operation_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", normalized):
            raise ValueError("invalid operation id")
        return normalized

    @staticmethod
    def _restore_is_blocking(status: RestoreStatus) -> bool:
        return status in {
            RestoreStatus.STAGED,
            RestoreStatus.RELOAD_SCHEDULED,
            RestoreStatus.APPLYING,
            RestoreStatus.VALIDATING,
            RestoreStatus.ROLLBACK_PENDING,
            RestoreStatus.ROLLING_BACK,
        }

    def get_maintenance_state(self) -> dict[str, object]:
        plan = self._read_plan()
        return {
            "blocked": bool(plan and self._restore_is_blocking(plan.status)),
            "operation_id": plan.operation_id if plan and self._restore_is_blocking(plan.status) else None,
            "status": plan.status.value if plan else None,
        }

    def has_pending_restores(self) -> bool:
        return bool(self.get_maintenance_state()["blocked"])

    def list_pending_restores(self) -> list[str]:
        plan = self._read_plan()
        if plan and self._restore_is_blocking(plan.status):
            return [plan.operation_id]
        return []

    def _public_restore_status(self, plan: RestorePlan) -> dict[str, object]:
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

    def get_restore_status(self, operation_id: str | None = None) -> dict[str, object] | None:
        plan = self._read_plan(operation_id)
        return self._public_restore_status(plan) if plan else None

    def _migrate_legacy_restore_files(self) -> RestorePlan | None:
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
            role = _BACKUP_FILE_SPECS.get(name, (FileRole.OPERATIONAL, "regular", False))[0]
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

    async def backup_if_needed_async(self) -> dict[str, object] | None:
        """异步版本：通过 asyncio.to_thread 将同步文件 I/O 卸载到线程池。"""
        return await asyncio.to_thread(self.backup_if_needed)

    async def create_backup(self, kind: str = "manual") -> dict[str, object]:
        """创建经过校验的备份，并把同步文件 I/O 放入线程池。"""
        try:
            backup_type = BackupType(kind)
        except ValueError as exc:
            raise BackupOperationError("invalid_backup_type") from exc
        async with self._operation_lock:
            return await asyncio.to_thread(self._create_backup_sync, backup_type, None)

    def _has_backup_data(self) -> bool:
        canonical = self.data_dir / "memora.db"
        return canonical.is_file() and not canonical.is_symlink()

    def _backup_name(self, backup_type: BackupType, previous_version: str | None) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        if backup_type is BackupType.VERSION_CHANGE:
            label = re.sub(r"[^A-Za-z0-9_.-]", "_", previous_version or "unknown")
            return f"v{label}_{suffix}"
        return f"{backup_type.value}_{timestamp}_{suffix}"

    def _create_backup_sync(
        self,
        backup_type: BackupType,
        previous_version: str | None,
    ) -> dict[str, object]:
        backups_root = self.data_dir / "backups"
        backups_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = backups_root / f".creating-{uuid.uuid4().hex}"
        final_name = self._backup_name(backup_type, previous_version)
        final_dir = backups_root / final_name
        temporary_dir.mkdir(parents=True, exist_ok=False)
        snapshots: dict[str, dict[str, object]] = {}
        total_size = 0
        try:
            required = self._estimated_backup_size()
            ensure_free_space(self.data_dir, required)
            for name, (role, kind, required_file) in _BACKUP_FILE_SPECS.items():
                source = self.data_dir / name
                if not source.exists():
                    if required_file:
                        raise BackupOperationError("canonical_file_missing")
                    continue
                if not source.is_file() or source.is_symlink():
                    raise BackupOperationError("backup_source_invalid")
                target = temporary_dir / name
                if kind == "sqlite":
                    result = snapshot_sqlite(source, target)
                else:
                    result = copy_regular_file(source, target, role=role)
                result_role = role if role is not FileRole.CANONICAL else result.role
                snapshots[name] = {
                    "role": result_role.value,
                    "kind": kind,
                    "size_bytes": result.size_bytes,
                    "sha256": result.sha256,
                    "quick_check": result.quick_check,
                }
                total_size += result.size_bytes

            timestamp = datetime.now(timezone.utc).isoformat()
            manifest = {
                "manifest_version": 2,
                "status": "ready",
                "backup_type": backup_type.value,
                "plugin_version": PLUGIN_VERSION,
                "previous_version": previous_version,
                "backup_timestamp": timestamp,
                "backup_unix_time": time.time(),
                "files": snapshots,
                "file_count": len(snapshots),
                "total_size_bytes": total_size,
                "warning_codes": [],
            }
            atomic_write_json(temporary_dir / _BACKUP_INFO_FILE, manifest)
            if final_dir.exists():
                raise BackupOperationError("backup_name_conflict")
            os.replace(temporary_dir, final_dir)
            return {
                "name": final_name,
                "directory": str(final_dir),
                "backup_type": backup_type.value,
                "backup_timestamp": timestamp,
                "plugin_version": PLUGIN_VERSION,
                "status": "ready",
                "integrity": BackupIntegrity.VERIFIED.value,
                "file_count": len(snapshots),
                "total_size_bytes": total_size,
                "warning_codes": [],
            }
        except BackupOperationError:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            logger.error(
                "[备份管理] 创建备份失败 operation=%s error_class=%s",
                final_name,
                type(exc).__name__,
            )
            raise BackupOperationError("backup_create_failed") from exc

    def _estimated_backup_size(self) -> int:
        return sum(
            path.stat().st_size
            for name in _BACKUP_FILE_SPECS
            if (path := self.data_dir / name).is_file() and not path.is_symlink()
        )

    def prune_backups(
        self, keep_days: int, now: float | None = None
    ) -> dict[str, object]:
        """清理到期 scheduled/pre_restore 备份，跳过活动事务引用。"""
        days = max(1, int(keep_days))
        cutoff = (time.time() if now is None else float(now)) - days * 86400
        referenced = self._referenced_backup_names()
        removed: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, str]] = []
        for item in self.list_backups(str(self.data_dir)):
            name = str(item.get("name", ""))
            if name in referenced or item.get("backup_type") not in {
                BackupType.SCHEDULED.value,
                BackupType.PRE_RESTORE.value,
            }:
                continue
            backup_dir = self.data_dir / "backups" / name
            try:
                if backup_dir.stat().st_mtime >= cutoff:
                    skipped.append(name)
                    continue
                self.validate_backup_name(name)
                shutil.rmtree(backup_dir)
                removed.append(name)
            except (OSError, ValueError) as exc:
                failed.append({"name": name, "reason_code": type(exc).__name__})
        return {"removed": removed, "skipped": skipped, "failed": failed}

    def _referenced_backup_names(self) -> set[str]:
        referenced: set[str] = set()
        root = self.data_dir / ".restore"
        for plan_path in root.glob("*/restore_plan.json"):
            try:
                value = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict):
                continue
            status = str(value.get("status", ""))
            if status in {
                RestoreStatus.SUCCEEDED.value,
                RestoreStatus.FAILED_BEFORE_APPLY.value,
                RestoreStatus.ROLLED_BACK.value,
                RestoreStatus.CANCELLED.value,
            }:
                continue
            referenced.update(
                name
                for name in (
                    str(value.get("source_backup_name", "")),
                    str(value.get("pre_restore_backup_name", "")),
                )
                if name
            )
        return referenced

    @staticmethod
    def validate_backup_name(name: str) -> str:
        """校验并规范化来自 API 输入的备份目录名。"""
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("backup name required")
        if normalized in {".", ".."}:
            raise ValueError("invalid backup name")
        if Path(normalized).is_absolute():
            raise ValueError("backup name must not be absolute")
        if any(sep and sep in normalized for sep in ("/", "\\", os.sep, os.altsep)):
            raise ValueError("backup name must not contain path separators")
        if Path(normalized).name != normalized:
            raise ValueError("invalid backup name")
        if not _BACKUP_NAME_RE.fullmatch(normalized):
            raise ValueError(
                "backup name may contain only letters, numbers, dot, underscore, and dash"
            )
        return normalized

    def get_backup_dir(self, name: str) -> Path | None:
        """返回已在后端备案的备份目录路径。"""
        backup_name = self.validate_backup_name(name)
        candidate = self.data_dir / "backups" / backup_name
        if candidate.is_symlink():
            resolved_candidate = candidate.resolve()
            backups_root = (self.data_dir / "backups").resolve()
            try:
                resolved_candidate.relative_to(backups_root)
            except ValueError as exc:
                raise ValueError("backup path escapes backups directory") from exc
            raise ValueError("backup directory must not be a symlink")
        legal_names = {
            str(item.get("name", ""))
            for item in self.list_backups(str(self.data_dir))
            if item.get("name")
        }
        if backup_name not in legal_names:
            return None
        backup_dir = (self.data_dir / "backups" / backup_name).resolve()
        backups_root = (self.data_dir / "backups").resolve()
        try:
            backup_dir.relative_to(backups_root)
        except ValueError as exc:
            raise ValueError("backup path escapes backups directory") from exc
        if not backup_dir.is_dir():
            return None
        return backup_dir

    def delete_backup(self, name: str) -> bool:
        """删除指定备份目录。返回 True 表示成功。"""
        backup_name = self.validate_backup_name(name)
        backup_dir = self.get_backup_dir(backup_name)
        if backup_dir is None:
            logger.warning(f"[BackupManager] 备份不存在: {backup_name}")
            return False
        try:
            shutil.rmtree(backup_dir)
            logger.info(f"[BackupManager] 删除备份: {backup_name}")
            return True
        except OSError as exc:
            logger.error(f"[BackupManager] 删除备份失败 {backup_name}: {exc}")
            raise

    def _quick_check(self, path: Path) -> str:
        connection = sqlite3.connect(str(path))
        try:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            connection.close()
        if result.lower() != "ok":
            raise BackupOperationError("backup_invalid")
        return result

    def _preflight_restore(self, backup_name: str) -> dict[str, object]:
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
        verified = info.get("manifest_version") == 2 and isinstance(manifest_files, dict)
        file_specs: list[dict[str, object]] = []
        if verified:
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
        return {
            "backup_name": backup_name,
            "backup_dir": backup_dir,
            "file_specs": file_specs,
            "integrity": integrity,
            "warning_codes": ["legacy_unverified"] if not verified else [],
        }

    def stage_restore(self, name: str, apply_mode: str = "restart") -> dict[str, object]:
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
                    "legacy_unverified" if preflight["integrity"] == "legacy_unverified" else None
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
        payload = plan_dir / "payload" / progress.name
        target = self.data_dir / progress.name
        previous = plan_dir / "previous" / progress.name
        previous.parent.mkdir(parents=True, exist_ok=True)
        if not payload.is_file() or payload.is_symlink():
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

    def _rollback_restore_files(self, plan: RestorePlan) -> None:
        plan_dir = self._restore_root() / plan.operation_id
        plan.status = RestoreStatus.ROLLING_BACK
        self._write_plan(plan)
        try:
            for progress in reversed(plan.files):
                target = self.data_dir / progress.name
                previous = plan_dir / "previous" / progress.name
                if progress.installed and target.exists():
                    target.unlink()
                if progress.moved_to_previous and previous.exists():
                    os.replace(previous, target)
            plan.status = RestoreStatus.ROLLED_BACK
            self._write_plan(plan)
        except OSError as exc:
            plan.status = RestoreStatus.ROLLBACK_PENDING
            plan.reason_code = "restore_rollback_pending"
            self._write_plan(plan)
            raise BackupOperationError("restore_rollback_pending") from exc

    def _validate_restored_files(self, plan: RestorePlan) -> None:
        for progress in plan.files:
            path = self.data_dir / progress.name
            if not path.is_file() or path.is_symlink():
                raise BackupOperationError("restore_apply_failed")
            if progress.name.endswith(".db"):
                self._quick_check(path)

    def apply_pending_restores(self) -> dict[str, object]:
        """在数据库打开前应用恢复计划，并在失败时回滚。"""
        plan = self._read_plan()
        if plan is None:
            plan = self._migrate_legacy_restore_files()
        if plan is None:
            return {"status": "none", "applied": 0}
        if plan.status is RestoreStatus.ROLLBACK_PENDING:
            self._rollback_restore_files(plan)
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
            self._validate_restored_files(plan)
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
            plan.reason_code = str(exc) if isinstance(exc, BackupOperationError) else "restore_apply_failed"
            if not started_apply:
                plan.status = RestoreStatus.FAILED_BEFORE_APPLY
                self._write_plan(plan)
                return self._public_restore_status(plan)
            try:
                self._rollback_restore_files(plan)
            except BackupOperationError:
                return self._public_restore_status(plan)
            return self._public_restore_status(plan)

    def mark_restore_succeeded(self, operation_id: str | None = None) -> None:
        plan = self._read_plan(operation_id)
        if plan is None:
            return
        plan.status = RestoreStatus.SUCCEEDED
        plan.reason_code = None
        self._write_plan(plan)
        shutil.rmtree(self._restore_root() / plan.operation_id / "payload", ignore_errors=True)
        shutil.rmtree(self._restore_root() / plan.operation_id / "previous", ignore_errors=True)

    def mark_restore_startup_failure_if_needed(self, failed: bool) -> None:
        if not failed:
            return
        plan = self._read_plan()
        if plan and plan.status is RestoreStatus.VALIDATING:
            plan.status = RestoreStatus.ROLLBACK_PENDING
            plan.reason_code = "restore_startup_failed"
            self._write_plan(plan)

    def mark_reload_scheduled(self, operation_id: str, scheduled: bool) -> None:
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
        if Path(name).name != name or Path(name).is_absolute():
            return False
        if any(sep and sep in name for sep in ("/", "\\", os.sep, os.altsep)):
            return False
        return any(fnmatch.fnmatch(name, pattern) for pattern in _BACKUP_PATTERNS)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def list_backups(data_dir: str) -> list[dict]:
        """枚举现有备份并返回不含服务器路径的摘要。"""
        backups_path = Path(data_dir) / "backups"
        if not backups_path.exists():
            return []

        result: list[dict] = []
        for backup_dir in sorted(backups_path.iterdir(), reverse=True):
            if (
                not backup_dir.is_dir()
                or backup_dir.is_symlink()
                or backup_dir.name.startswith(".creating-")
            ):
                continue
            info_path = backup_dir / _BACKUP_INFO_FILE
            info: dict = {}
            if info_path.exists():
                with contextlib.suppress(json.JSONDecodeError, OSError):
                    info = json.loads(info_path.read_text(encoding="utf-8"))
            if not isinstance(info, dict):
                info = {}
            files: list[str] = []
            invalid = False
            for item in backup_dir.iterdir():
                if item.name == _BACKUP_INFO_FILE:
                    continue
                if item.is_symlink():
                    invalid = True
                    continue
                if item.is_file():
                    files.append(item.name)

            manifest_version = info.get("manifest_version")
            is_manifest = manifest_version == 2 and isinstance(info.get("files"), dict)
            if is_manifest:
                manifest_files = info["files"]
                missing = [name for name in manifest_files if name not in files]
                invalid = invalid or bool(missing)
                integrity = (
                    BackupIntegrity.INVALID.value
                    if invalid or info.get("status") != "ready"
                    else BackupIntegrity.VERIFIED.value
                )
                backup_status = "invalid" if invalid else str(info.get("status", "ready"))
                file_count = len(manifest_files)
                total_size = int(info.get("total_size_bytes", 0) or 0)
                warning_codes = list(info.get("warning_codes", []) or [])
            else:
                integrity = BackupIntegrity.LEGACY_UNVERIFIED.value
                backup_status = "ready"
                file_count = len(files)
                total_size = sum(
                    item.stat().st_size
                    for item in backup_dir.iterdir()
                    if item.is_file() and item.name != _BACKUP_INFO_FILE
                )
                warning_codes = ["legacy_unverified"]

            backup_type = str(
                info.get(
                    "backup_type",
                    "version_change" if backup_dir.name.startswith("v") else "manual",
                )
            )
            result.append(
                {
                    "name": backup_dir.name,
                    "backup_type": backup_type,
                    "created_at": info.get("backup_timestamp"),
                    "backup_timestamp": info.get("backup_timestamp"),
                    "plugin_version": info.get("plugin_version"),
                    "manifest_version": manifest_version,
                    "status": backup_status,
                    "integrity": integrity,
                    "files": sorted(files),
                    "file_count": file_count,
                    "total_size_bytes": total_size,
                    "warning_codes": warning_codes,
                    "can_restore": integrity != BackupIntegrity.INVALID.value,
                    "can_hot_restore": integrity != BackupIntegrity.INVALID.value,
                }
            )

        return result


__all__ = ["BackupManager", "PLUGIN_VERSION"]
