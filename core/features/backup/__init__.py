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
from .infrastructure import (
    atomic_write_json,
    copy_regular_file,
    ensure_free_space,
    sha256_file,
    snapshot_sqlite,
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
    "atomic_write_json",
    "copy_regular_file",
    "ensure_free_space",
    "sha256_file",
    "snapshot_sqlite",
]
