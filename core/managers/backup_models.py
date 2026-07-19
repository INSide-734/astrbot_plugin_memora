"""备份与恢复领域模型。

这些类型只描述备份操作的稳定边界；页面 API 应由管理器生成脱敏字典，
不要直接把 ``Path`` 或内部事务日志暴露给调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class BackupType(StrEnum):
    """备份来源类型。"""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    VERSION_CHANGE = "version_change"
    PRE_RESTORE = "pre_restore"


class FileRole(StrEnum):
    """备份文件在恢复链中的职责。"""

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
    """恢复事务对外公开的阶段。"""

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
    """备份/恢复操作失败，异常消息只允许稳定的用户安全摘要。"""


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
    """恢复事务中单个文件的持久化进度。"""

    name: str
    role: FileRole
    moved_to_previous: bool = False
    installed: bool = False
    validated: bool = False


@dataclass
class RestorePlan:
    """恢复事务计划的内存表示。"""

    operation_id: str
    source_backup_name: str
    apply_mode: str
    status: RestoreStatus
    files: list[RestoreFileProgress] = field(default_factory=list)
    pre_restore_backup_name: str | None = None
    reason_code: str | None = None
    reload_scheduled: bool = False
    requires_manual_restart: bool = False


__all__ = [
    "BackupOperationError",
    "BackupIntegrity",
    "BackupType",
    "FileRole",
    "RestoreFileProgress",
    "RestorePlan",
    "RestoreStatus",
    "SnapshotResult",
]
