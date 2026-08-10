"""备份 feature 的基础设施实现。"""

from .integrity import (
    FEEDBACK_HMAC_KEY_NAME,
    FEEDBACK_SIGNAL_DB_NAME,
    OPERATIONAL_BACKUP_PATTERNS,
    OPERATIONAL_FILE_SPECS,
    finalize_snapshot_file,
    prepare_feedback_backup,
    rollback_restore_files,
    validate_feedback_backup_files,
    validate_feedback_backup_specs,
    validate_feedback_hmac_pair,
    validate_feedback_restore_files,
    validate_feedback_snapshot,
    validate_quarantine_references,
    validate_restored_files,
)
from .snapshot import (
    atomic_write_json,
    copy_regular_file,
    ensure_free_space,
    sha256_file,
    snapshot_sqlite,
)

__all__ = [
    "FEEDBACK_HMAC_KEY_NAME",
    "FEEDBACK_SIGNAL_DB_NAME",
    "OPERATIONAL_BACKUP_PATTERNS",
    "OPERATIONAL_FILE_SPECS",
    "atomic_write_json",
    "copy_regular_file",
    "ensure_free_space",
    "finalize_snapshot_file",
    "prepare_feedback_backup",
    "rollback_restore_files",
    "sha256_file",
    "snapshot_sqlite",
    "validate_feedback_backup_files",
    "validate_feedback_backup_specs",
    "validate_feedback_hmac_pair",
    "validate_feedback_restore_files",
    "validate_feedback_snapshot",
    "validate_quarantine_references",
    "validate_restored_files",
]
