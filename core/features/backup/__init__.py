"""备份 feature 的公开边界。"""

from .domain import (
    BackupIntegrity,
    BackupOperationError,
    BackupType,
    FileRole,
    RestoreFileProgress,
    RestorePlan,
    RestoreStatus,
    SnapshotResult,
)

__all__ = [
    "BackupIntegrity",
    "BackupOperationError",
    "BackupType",
    "FileRole",
    "RestoreFileProgress",
    "RestorePlan",
    "RestoreStatus",
    "SnapshotResult",
]
