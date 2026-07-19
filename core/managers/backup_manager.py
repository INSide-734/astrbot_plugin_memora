"""版本变更触发的数据备份管理器。"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import logger

from .backup_models import FileRole
from ..utils.version import PLUGIN_VERSION  # single source of truth: metadata.yaml

_VERSION_FILE = ".plugin_version"
_BACKUP_INFO_FILE = "backup_info.json"
_BACKUP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

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

# 新格式备份使用固定文件规格；旧 glob 模式仍保留给兼容路径。
_BACKUP_FILE_SPECS: dict[str, tuple[FileRole, str, bool]] = {
    "memora.db": (FileRole.CANONICAL, "sqlite", True),
    "conversations.db": (FileRole.OPERATIONAL, "sqlite", False),
    "decay_state.json": (FileRole.OPERATIONAL, "regular", False),
    "memora.index": (FileRole.DERIVED, "regular", False),
    "memora_graph.index": (FileRole.DERIVED, "regular", False),
    "memora_graph_documents.db": (FileRole.DERIVED, "sqlite", False),
}


class BackupManager:
    """检测版本变化并创建完整数据备份。"""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.version_file = self.data_dir / _VERSION_FILE
        # 备份、恢复和清理操作共用同一把异步锁；同步启动应用不依赖此锁。
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

    def backup_if_needed(self) -> str | None:
        """在版本变更时创建完整备份，并返回备份目录路径。"""
        if not self.needs_backup():
            return None

        stored = self.get_stored_version()
        old_label = stored or "unknown"
        backup_dir = self.data_dir / "backups" / f"v{old_label}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"[BackupManager] 检测到版本变更 ({old_label} → {PLUGIN_VERSION})，"
            f"正在备份数据到 {backup_dir} ..."
        )

        copied_count = 0
        for pattern in _BACKUP_PATTERNS:
            for file_path in self.data_dir.glob(pattern):
                if not file_path.is_file():
                    continue
                dest = backup_dir / file_path.name
                try:
                    shutil.copy2(file_path, dest)
                    copied_count += 1
                except OSError as exc:
                    logger.error(
                        f"[BackupManager] 备份文件失败 {file_path.name}: {exc}"
                    )

        # 写入备份元数据
        info = {
            "plugin_version": PLUGIN_VERSION,
            "previous_version": old_label,
            "backup_timestamp": datetime.now(timezone.utc).isoformat(),
            "backup_unix_time": time.time(),
            "files_copied": copied_count,
            "data_dir": str(self.data_dir),
        }
        info_path = backup_dir / _BACKUP_INFO_FILE
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 仅在备份成功后更新版本记录
        self.write_current_version()

        logger.info(f"[BackupManager] 备份完成: {copied_count} 个文件 → {backup_dir}")
        return str(backup_dir)

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

    async def backup_if_needed_async(self) -> str | None:
        """异步版本：通过 asyncio.to_thread 将同步文件 I/O 卸载到线程池。"""
        return await asyncio.to_thread(self.backup_if_needed)

    async def create_backup(self) -> str:
        """按需创建备份（不受版本变更限制）。

        与 backup_if_needed 不同，此方法始终创建备份，
        使用当前时间戳作为备份目录名。
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = self.data_dir / "backups" / f"manual_{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"[BackupManager] 按需备份: {backup_dir}")

        copied_count = 0
        for pattern in _BACKUP_PATTERNS:
            for file_path in self.data_dir.glob(pattern):
                if not file_path.is_file():
                    continue
                dest = backup_dir / file_path.name
                try:
                    shutil.copy2(file_path, dest)
                    copied_count += 1
                except OSError as exc:
                    logger.error(
                        f"[BackupManager] 备份文件失败 {file_path.name}: {exc}"
                    )

        info = {
            "plugin_version": PLUGIN_VERSION,
            "backup_type": "manual",
            "backup_timestamp": datetime.now(timezone.utc).isoformat(),
            "backup_unix_time": time.time(),
            "files_copied": copied_count,
            "data_dir": str(self.data_dir),
        }
        info_path = backup_dir / _BACKUP_INFO_FILE
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        logger.info(f"[BackupManager] 备份完成: {copied_count} 个文件 → {backup_dir}")
        return str(backup_dir)

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
        """枚举现有备份及其元数据。"""
        backups_path = Path(data_dir) / "backups"
        if not backups_path.exists():
            return []

        result: list[dict] = []
        for backup_dir in sorted(backups_path.iterdir(), reverse=True):
            if not backup_dir.is_dir():
                continue
            info_path = backup_dir / _BACKUP_INFO_FILE
            info: dict = {}
            if info_path.exists():
                with contextlib.suppress(json.JSONDecodeError, OSError):
                    info = json.loads(info_path.read_text(encoding="utf-8"))
            info.setdefault("directory", str(backup_dir))
            info.setdefault("name", backup_dir.name)
            files = [p.name for p in backup_dir.iterdir() if p.is_file()]
            info.setdefault("files", files)
            info.setdefault("file_count", len(files))
            result.append(info)

        return result


__all__ = ["BackupManager", "PLUGIN_VERSION"]
