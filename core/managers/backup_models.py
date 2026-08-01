"""备份与恢复事务的共享类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class BackupType(StrEnum):
    """备份来源类型。"""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    VERSION_CHANGE = "version_change"
    PRE_MIGRATION = "pre_migration"
    PRE_RESTORE = "pre_restore"


class FileRole(StrEnum):
    """备份文件在数据链中的职责。"""

    CANONICAL = "canonical"
    OPERATIONAL = "operational"
    DERIVED = "derived"


class BackupIntegrity(StrEnum):
    """备份 manifest 的完整性状态。"""

    VERIFIED = "verified"
    LEGACY_UNVERIFIED = "legacy_unverified"
    INVALID = "invalid"
    INCOMPATIBLE = "incompatible"


class RestoreStatus(StrEnum):
    """恢复事务对外暴露的阶段。"""

    STAGED = "staged"
    RELOAD_SCHEDULED = "reload_scheduled"
    APPLYING = "applying"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED_BEFORE_APPLY = "failed_before_apply"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class BackupOperationError(RuntimeError):
    """备份或恢复操作失败。"""


@dataclass(frozen=True)
class SnapshotResult:
    """单个快照文件的校验结果。"""

    name: str
    role: FileRole
    source: Path
    target: Path
    size_bytes: int
    sha256: str
    quick_check: str | None = None


@dataclass(frozen=True)
class RestoreFileProgress:
    """恢复事务中单个文件的应用进度。"""

    name: str
    role: FileRole
    moved_to_previous: bool = False
    installed: bool = False
    validated: bool = False


@dataclass
class RestorePlan:
    """可持久化的恢复计划。"""

    operation_id: str
    source_backup_name: str
    apply_mode: str
    status: RestoreStatus
    files: list[RestoreFileProgress] = field(default_factory=list)
    pre_restore_backup_name: str | None = None
    reason_code: str | None = None
    reload_scheduled: bool = False
    requires_manual_restart: bool = False
