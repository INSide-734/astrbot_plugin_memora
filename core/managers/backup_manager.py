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

from .backup_models import BackupIntegrity, BackupOperationError, BackupType, FileRole
from .backup_snapshot import (
    atomic_write_json,
    copy_regular_file,
    ensure_free_space,
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

    def apply_pending_restores(self) -> int:
        """启动时检查并应用 .restore 暂存文件。

        当 restore_backup 因文件锁定而无法直接覆盖时，
        备份文件会被写入 <name>.restore 后缀。
        此方法在插件启动、数据库尚未打开时应用这些暂存文件。
        返回已应用的文件数。
        """
        applied = 0
        for restore_file in self.data_dir.glob("*.restore"):
            target = restore_file.with_suffix("")  # 去掉 .restore 后缀
            try:
                if target.exists():
                    target.unlink()
                shutil.move(str(restore_file), str(target))
                applied += 1
                logger.info(f"[BackupManager] 已应用恢复暂存: {target.name}")
            except OSError as exc:
                logger.error(
                    f"[BackupManager] 应用恢复暂存失败 {restore_file.name}: {exc}"
                )
        return applied

    def has_pending_restores(self) -> bool:
        """若存在待应用的恢复暂存文件，则返回 ``True``。"""
        return any(self.data_dir.glob("*.restore"))

    def list_pending_restores(self) -> list[str]:
        """返回等待下次启动时应用的恢复暂存文件名列表。"""
        return sorted(p.name for p in self.data_dir.glob("*.restore") if p.is_file())

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
        return any(
            (self.data_dir / name).is_file() and not (self.data_dir / name).is_symlink()
            for name in _BACKUP_FILE_SPECS
        )

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
        plan = self.data_dir / ".restore" / "restore_plan.json"
        if not plan.exists():
            return set()
        try:
            value = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(value, dict):
            return set()
        names = {
            str(value.get("source_backup_name", "")),
            str(value.get("pre_restore_backup_name", "")),
        }
        return {name for name in names if name}

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

    def stage_restore(self, name: str) -> dict[str, object]:
        """将合法备份目录中的文件暂存为 ``*.restore`` 恢复文件。"""
        backup_name = self.validate_backup_name(name)
        backup_dir = self.get_backup_dir(backup_name)
        if backup_dir is None:
            raise FileNotFoundError(f"backup not found: {backup_name}")

        staged: list[str] = []
        skipped: list[str] = []
        for src in backup_dir.iterdir():
            if not src.is_file() or src.name == _BACKUP_INFO_FILE:
                continue
            if not self._is_restorable_file_name(src.name):
                skipped.append(src.name)
                continue
            dst = self.data_dir / src.name
            restore_tmp = dst.with_name(dst.name + ".restore")
            try:
                shutil.copy2(src, restore_tmp)
                staged.append(restore_tmp.name)
            except OSError as exc:
                logger.error(f"[BackupManager] 暂存恢复文件失败 {src.name}: {exc}")
                skipped.append(src.name)

        logger.info(
            f"[BackupManager] 已暂存备份恢复 {backup_name}: "
            f"staged={len(staged)}, skipped={len(skipped)}"
        )
        return {
            "name": backup_name,
            "staged": len(staged),
            "skipped": len(skipped),
            "staged_files": staged,
            "skipped_files": skipped,
            "pending": bool(staged),
        }

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
