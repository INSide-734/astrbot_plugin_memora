"""备份 feature 的领域异常。"""


class BackupOperationError(RuntimeError):
    """备份或恢复操作失败。"""


__all__ = ["BackupOperationError"]
