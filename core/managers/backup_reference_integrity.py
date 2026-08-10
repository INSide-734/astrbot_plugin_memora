"""备份恢复完整性实现的旧路径兼容导出。"""

from ..features.backup.infrastructure.integrity import (
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

__all__ = [
    "FEEDBACK_HMAC_KEY_NAME",
    "FEEDBACK_SIGNAL_DB_NAME",
    "OPERATIONAL_BACKUP_PATTERNS",
    "OPERATIONAL_FILE_SPECS",
    "finalize_snapshot_file",
    "prepare_feedback_backup",
    "rollback_restore_files",
    "validate_feedback_backup_files",
    "validate_feedback_backup_specs",
    "validate_feedback_hmac_pair",
    "validate_feedback_restore_files",
    "validate_feedback_snapshot",
    "validate_quarantine_references",
    "validate_restored_files",
]
