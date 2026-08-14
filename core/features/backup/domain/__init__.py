"""备份 feature 的领域契约。"""

from .errors import BackupOperationError
from .models import (
    BackupIntegrity,
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
